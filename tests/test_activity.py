"""Private metadata accounting; no raw request/response or console capture."""
from concurrent.futures import ThreadPoolExecutor
import io
import json
import time
import unittest
from unittest.mock import patch
import zipfile

import lab_backup
import lab_server
import test_web_auth
import web_auth


class ActivityTests(unittest.TestCase):
    setUp = test_web_auth.AuthTests.setUp
    tearDown = test_web_auth.AuthTests.tearDown
    node = staticmethod(test_web_auth.AuthTests.node)
    enable = test_web_auth.AuthTests.enable
    http = test_web_auth.AuthTests.http
    login = test_web_auth.AuthTests.login

    def records(self, cookie):
        status, _, page = self.http('/api/activity',cookie=cookie)
        self.assertEqual(status,200)
        return page['records']

    def test_roles_operations_and_no_secrets_or_arbitrary_payloads(self):
        self.assertEqual(self.http('/api/activity')[0],404)
        self.enable()
        self.assertEqual(self.http('/api/activity')[0],401)
        admin, csrf = self.login()
        writer, writer_csrf = self.login('user')
        self.assertEqual(self.http('/api/activity',cookie=writer)[0],403)
        self.assertEqual(self.http('/api/activity',cookie=admin,headers={'Origin':'https://foreign.invalid'})[0],409)
        secret = 'never-store-this-private-text'
        self.assertEqual(self.http('/api/instructions','PUT',{'revision':'0','file':{'name':secret+'.md','text':secret}},admin,csrf)[0],200)
        self.assertEqual(self.http('/api/topology','PUT',{'secret':secret},writer,writer_csrf)[0],403)
        self.assertEqual(self.http('/api/nodes/r1/stop','POST',{},writer,writer_csrf)[0],200)
        self.assertEqual(self.http('/api/images/'+secret,'POST',{},writer,writer_csrf)[0],403)
        self.assertEqual(self.http('/api/'+secret,'POST',{'password':secret},admin,csrf)[0],403)
        self.assertEqual(self.http('/api/instructions','PUT',{'secret':secret},admin,csrf)[0],400)
        before = self.records(admin)
        for _ in range(3): self.http('/api/state',cookie=admin)
        self.assertEqual(self.records(admin),before)
        text = json.dumps(before)
        for value in [secret, admin.split('=',1)[1], writer.split('=',1)[1], csrf, writer_csrf,
                      web_auth.digest(admin.split('=',1)[1]), *test_web_auth.PASSWORDS.values()]:
            self.assertNotIn(value,text)
        by_operation = {row['operation']:row for row in reversed(before)}
        self.assertEqual(by_operation['node.stop']['account'],'user')
        self.assertEqual(by_operation['node.stop']['target'],'r1')
        self.assertEqual(by_operation['node.stop']['result'],'http_ok')
        self.assertEqual(by_operation['topology.save']['result'],'denied')
        self.assertEqual(by_operation['instructions.update']['result'],'failed')
        self.assertTrue(all(row['finished'] for row in before))
        self.assertEqual(self.http('/api/activity?account=admin&account=user',cookie=admin)[0],409)
        self.assertEqual(self.http('/api/activity?before=-1',cookie=admin)[0],409)
        self.assertEqual(self.http('/api/activity?source=invalid',cookie=admin)[0],409)

    def test_signin_claims_separate_sessions_logout_and_restart(self):
        self.enable()
        auth = self.server.auth
        status = self.http('/api/auth/login','POST',{'name':'admin','password':'bad'},headers={'X-Weblab-Login':'1'})[0]
        self.assertEqual(status,401)
        status = self.http('/api/auth/login','POST',{'name':'private-unknown-name','password':'bad'},headers={'X-Weblab-Login':'1'})[0]
        self.assertEqual(status,401)
        first, csrf = self.login()
        second, _ = self.login()
        self.http('/api/auth/logout','POST',{},first,csrf)
        rows = self.records(second)
        succeeded = [row for row in rows if row['operation']=='auth.login' and row['result']=='http_ok']
        failed = [row for row in rows if row['operation']=='auth.login' and row['result']=='denied']
        self.assertEqual(len({row['session_ref'] for row in succeeded}),2)
        self.assertTrue(all(row['session_ref'] is None for row in failed))
        self.assertEqual({row['account'] for row in failed},{None,'admin'})
        self.assertEqual(rows[0]['operation'],'auth.logout')
        restarted = web_auth.Auth(self.lab.directory/'.auth',test_web_auth.PASSWORDS)
        try: self.assertEqual(restarted.activity_page({})['records'],rows)
        finally: restarted.close()

    def test_concurrent_pages_filters_retention_and_unfinished_record(self):
        self.enable()
        auth = self.server.auth
        def record(i):
            ident = auth.activity_begin('user','owner','browser','node.stop','r1')
            auth.activity_finish(ident,'http_ok',200)
        with ThreadPoolExecutor(5) as pool: list(pool.map(record,range(110)))
        first = auth.activity_page({'account':['user']})
        second = auth.activity_page({'account':['user'],'before':[str(first['next_before'])]})
        self.assertEqual(len(first['records']),100)
        self.assertEqual(len(second['records']),10)
        self.assertFalse(set(r['id'] for r in first['records']) & set(r['id'] for r in second['records']))
        self.assertEqual(auth.activity_page({'source':['agent']})['records'],[])
        pending = auth.activity_begin('admin','another','agent','tool.apply')
        self.assertEqual(auth.activity_page({})['records'][0]['result'],'unfinished')
        with patch.object(web_auth,'ACTIVITY_LIMIT',4):
            self.assertEqual(len(auth.activity_page({})['records']),4)
        with auth.lock, auth.db:
            auth.db.execute('UPDATE activity SET started=? WHERE id=?',(time.time()-31*86400,pending))
        self.assertNotIn(pending,[r['id'] for r in auth.activity_page({})['records']])

    def test_export_excludes_and_import_preserves_records(self):
        self.enable()
        admin, _ = self.login()
        before = self.records(admin)
        exported = lab_backup.create(self.lab)
        stream, _ = lab_backup.take(self.lab,exported['url'].rsplit('/',1)[1])
        with stream: data = stream.read()
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            self.assertFalse(any('auth' in name or 'activity' in name for name in archive.namelist()))
        lab_backup.restore(self.lab,io.BytesIO(data),lab_server.run)
        self.assertEqual(self.records(admin),before)

    def test_audit_write_failure_does_not_prevent_logout_and_is_visible(self):
        self.enable()
        admin, csrf = self.login()
        with patch.object(self.server.auth,'_prune_activity',side_effect=web_auth.sqlite3.OperationalError('no space')):
            self.assertEqual(self.http('/api/auth/logout','POST',{},admin,csrf)[0],200)
        self.assertEqual(self.http('/api/state',cookie=admin)[0],401)
        admin, _ = self.login()
        self.assertTrue(self.http('/api/activity',cookie=admin)[2]['recording_error'])


if __name__ == '__main__': unittest.main()
