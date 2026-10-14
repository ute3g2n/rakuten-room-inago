import { access, readFile } from 'node:fs/promises';
import { execFile } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import { promisify } from 'node:util';

const execFileAsync = promisify(execFile);

export async function restrictWindowsCredentialFile(filePath, {
  platform = process.platform, environment = process.env, execFileImpl = execFileAsync,
} = {}) {
  if (platform !== 'win32') return;
  const { USERDOMAIN, USERNAME } = environment;
  if (!USERDOMAIN || !USERNAME) throw new Error('Windowsユーザー名を取得できません');
  const account = `${USERDOMAIN}\\${USERNAME}`;
  try {
    await execFileImpl('icacls.exe', [filePath, '/inheritance:r', '/grant:r',
      `${account}:F`, 'SYSTEM:F', 'Administrators:F', '/C', '/Q']);
    await execFileImpl('icacls.exe', [filePath, '/remove:g',
      '*S-1-1-0', '*S-1-5-11', '*S-1-5-32-545', '/C', '/Q']);
  } catch {
    throw new Error('楽天API認証情報ファイルのアクセス権を制限できませんでした');
  }
}

export async function loadRakutenCredentials() {
  const credentialsPath = new URL('../rakuten-api.credentials.json', import.meta.url);
  let fileCredentials = {};

  try {
    await access(credentialsPath);
    await restrictWindowsCredentialFile(fileURLToPath(credentialsPath));
    const contents = await readFile(credentialsPath, 'utf8');
    fileCredentials = JSON.parse(contents);
  } catch (error) {
    if (error?.code !== 'ENOENT') {
      if (error instanceof SyntaxError) {
        throw new Error('rakuten-api.credentials.json のJSON形式が不正です');
      }
      throw new Error('楽天API認証情報ファイルを読み込めませんでした');
    }
  }

  if (!fileCredentials || typeof fileCredentials !== 'object' || Array.isArray(fileCredentials)) {
    throw new Error('rakuten-api.credentials.json はJSONオブジェクトで指定してください');
  }

  const credentials = {
    applicationId: process.env.RAKUTEN_APP_ID || fileCredentials.applicationId,
    accessKey: process.env.RAKUTEN_ACCESS_KEY || fileCredentials.accessKey,
  };
  if (typeof credentials.applicationId !== 'string' || !credentials.applicationId.trim() ||
      typeof credentials.accessKey !== 'string' || !credentials.accessKey.trim()) {
    throw new Error('rakuten-api.credentials.json に applicationId と accessKey を入力してください');
  }
  return credentials;
}
