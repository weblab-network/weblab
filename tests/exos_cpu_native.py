#!/usr/bin/env python3
"""Opt-in EXOS 33.1 CPU-name regression using two disposable KVM guests.

Requires root, QEMU, Perl, and the user-supplied pinned EXOS image. No Docker
or active lab is used. Reproduce the bad CPU-name boot, then verify the profile.
"""
import argparse
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import lab_server, vios
from tools.build_exos_demo import EXOSConsole
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('image', type=Path, help='EXOS-VM_33.1.1.31.qcow2')
image = parser.parse_args().image.resolve(strict=True)
if image.name != 'EXOS-VM_33.1.1.31.qcow2':
    parser.error('This probe targets EXOS-VM_33.1.1.31.qcow2')
original=vios.command

def amd_name(*args,**kwargs):
    cmd=original(*args,**kwargs)
    cmd[cmd.index('-cpu')+1]='host,model-id=AMD EPYC test CPU'
    return cmd

with tempfile.TemporaryDirectory(prefix='weblab-exos-cpu-') as directory:
    root=Path(directory)
    images=root/'images';images.mkdir()
    (images/image.name).symlink_to(image)
    lab=lab_server.Lab(root/'data',images)
    try:
        for name,command in [('amd-name',amd_name),('compatible-name',original)]:
            lab.save({'name':'CPU probe','nodes':[{'id':name,'name':'CPU-probe','type':'switch','image':image.name,'ethernet':3}], 'links':[]})
            with patch.object(vios,'command',side_effect=command):
                lab.start(name)
            console=EXOSConsole(lab,name)
            try:
                if name=='amd-name':
                    output=console.wait(r'===== developer menu =====[\s\S]*~>\s*$',timeout=180)
                    assert 'Could not determine the CPU Family' in output, output[-2500:]
                    print('PASS: AMD model-name reproduces CPU-family warning and developer-menu stop without sending input',flush=True)
                else:
                    console.login(fresh=True)
                    print(console.command('show version'),flush=True)
                    print('PASS: compatible model-name reaches EXOS CLI',flush=True)
            finally:
                console.close()
            lab.stop(name)
    finally:
        lab.stop_all();lab.file_lock.close()
