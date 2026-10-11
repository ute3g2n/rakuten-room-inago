import test from 'node:test';
import assert from 'node:assert/strict';

import { restrictWindowsCredentialFile } from '../scripts/rakuten-credentials.mjs';

test('Windows資格情報ファイルを読む前に継承ACLを外して本人・SYSTEM・管理者へ制限する', async () => {
  const calls = [];
  await restrictWindowsCredentialFile('C:\\dummy\\credential.json', {
    platform: 'win32', environment: { USERDOMAIN: 'TEST', USERNAME: 'User' },
    execFileImpl: async (command, args) => calls.push({ command, args }),
  });
  assert.equal(calls.length, 2);
  assert.equal(calls[0].command, 'icacls.exe');
  assert.deepEqual(calls[0].args.slice(1, 3), ['/inheritance:r', '/grant:r']);
  assert.deepEqual(calls[0].args.slice(3, 6), ['TEST\\User:F', 'SYSTEM:F', 'Administrators:F']);
  assert.deepEqual(calls[1].args.slice(1, 5), ['/remove:g', '*S-1-1-0', '*S-1-5-11', '*S-1-5-32-545']);
  await assert.rejects(restrictWindowsCredentialFile('dummy', {
    platform: 'win32', environment: {}, execFileImpl: async () => {},
  }), /Windowsユーザー名/);
  await assert.rejects(restrictWindowsCredentialFile('dummy', {
    platform: 'win32', environment: { USERDOMAIN: 'TEST', USERNAME: 'User' },
    execFileImpl: async () => { throw new Error('access denied'); },
  }), /アクセス権/);
});
