"""Console-only logout recovery without starting a Docker daemon or guest."""
import unittest
from unittest.mock import Mock, patch

import container_console


class ContainerConsoleTests(unittest.TestCase):
    @patch('container_console.shutil.which', return_value='/usr/bin/docker')
    def test_logout_reopens_same_shell_but_docker_failure_does_not_retry(self, which):
        run, sleep = Mock(side_effect=[0, 0, 125]), Mock()
        result = container_console.supervise('test-pc', run=run,
                                            clock=Mock(side_effect=[0, 20]), sleep=sleep)
        self.assertEqual(result, 125)
        self.assertEqual(sleep.call_count, 2)
        for call in run.call_args_list:
            self.assertEqual(call.args[0], ['/usr/bin/docker', 'exec', '-it', 'test-pc', '/bin/sh'])

    @patch('container_console.shutil.which', return_value='/usr/bin/docker')
    def test_repeated_rapid_exit_is_bounded(self, which):
        run, sleep = Mock(return_value=0), Mock()
        self.assertEqual(container_console.supervise('test-pc', run=run,
                         clock=Mock(side_effect=[0, 1, 2]), sleep=sleep), 1)
        self.assertEqual(run.call_count, 3)
        self.assertEqual(sleep.call_count, 2)

    @patch('container_console.shutil.which', return_value='/usr/bin/docker')
    def test_killed_shell_is_not_retried(self, which):
        run = Mock(return_value=-15)
        self.assertEqual(container_console.supervise('test-pc', run=run), 143)
        run.assert_called_once()
