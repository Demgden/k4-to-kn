/* 喵链接生成器 —— 把本地 .bcmkn 传到 codemao CDN，得到一个可导入的网址。
 *
 * 用法：
 *   1. 浏览器打开 https://kn.codemao.cn 并确保已登录（同站 cookie 是上传的前提）
 *   2. F12 → Console，整段粘贴回车
 *   3. 弹出的文件选择框里选 out/florr.k4kn.bcmkn
 *   4. 控制台会打印
 *        https://creation.bcmcdn.com/922/user-files/<内容哈希>.bcmkn
 *      并自动复制到剪贴板；把它粘进编辑器「导入喵链接」即可
 *
 * 原理（与 KN 编辑器上传作品的代码完全一致）：
 *   key   = "922/user-files/" + base64url(前缀 + SHA1(文件)) + ".bcmkn"
 *           前缀 = "16"（≤4MB，单块）或 "96"（>4MB，先按 4MB 分块各算 SHA1 再拼起来算一次）
 *   token = GET https://open-service.codemao.cn/cdn/qi-niu/tokens/uploading
 *             ?cdnName=qiniu&insertOnly=true&filePaths=<key>&projectName=neko
 *   PUT   = POST <upload_url>  multipart: key / file / token
 *   最终  = <bucket_url> + <key>
 *
 * 因为 key 只取决于文件内容，所以同一份文件重复跑不会产生第二份副本。
 */
(async () => {
  const KEY_CHUNK = 4194304;                       // 4MB，与编辑器一致
  const TOKEN_API = 'https://open-service.codemao.cn/cdn/qi-niu/tokens/uploading';
  const BUCKET_DIR = '922';                        // x.neko = 922
  const PROJECT = 'neko';

  const pick = () => new Promise((res) => {
    const inp = document.createElement('input');
    inp.type = 'file';
    inp.accept = '.bcmkn,.bcmknk,.bcmknh,.json';
    inp.onchange = () => res(inp.files[0]);
    inp.click();
  });

  const sha1 = async (buf) =>
    new Uint8Array(await crypto.subtle.digest('SHA-1', buf));

  const b64url = (bytes) =>
    btoa(String.fromCharCode(...bytes)).replace(/\//g, '_').replace(/\+/g, '-');

  async function fileKey(file) {
    const buf = await file.arrayBuffer();
    const n = Math.max(1, Math.ceil(buf.byteLength / KEY_CHUNK));
    const digests = [];
    for (let i = 0; i < n; i++) {
      digests.push(await sha1(buf.slice(i * KEY_CHUNK, (i + 1) * KEY_CHUNK)));
    }
    let prefix = '16';
    let body = digests[0];
    if (n > 1) {
      prefix = '96';
      const all = new Uint8Array(n * 20);
      digests.forEach((d, i) => all.set(d, i * 20));
      body = await sha1(all);
    }
    const hex = prefix + [...body].map((b) => b.toString(16).padStart(2, '0')).join('');
    const raw = new Uint8Array(hex.length / 2);
    for (let i = 0; i < raw.length; i++) raw[i] = parseInt(hex.substr(i * 2, 2), 16);
    return b64url(raw);
  }

  const file = await pick();
  if (!file) return console.warn('没选文件');

  const hash = await fileKey(file);
  const ext = (file.name.split('.').pop() || 'bcmkn').toLowerCase();
  const path = `${BUCKET_DIR}/user-files/${hash}.${ext}`;
  console.log('%c内容哈希 →', 'color:#0a0', path);

  const qs = new URLSearchParams({
    cdnName: 'qiniu',
    insertOnly: 'true',
    filePaths: path,
    projectName: PROJECT,
  });
  const got = await fetch(`${TOKEN_API}?${qs}`, { credentials: 'include' });
  if (!got.ok) {
    console.error('取上传凭证失败（多半是没登录）', got.status, await got.text());
    return;
  }
  const info = await got.json();
  const url = info.bucket_url + path;
  const token = (info.tokens || []).find((t) => t.file_path === path)?.token;
  if (!token) return console.error('凭证里没有这个文件路径：', info);

  console.log('%c上传中…', 'color:#08f', file.size, '字节');
  const form = new FormData();
  form.append('key', path);
  form.append('file', file);
  form.append('token', token);
  const up = await fetch(info.upload_url || 'https://upload.qiniup.com/', {
    method: 'POST',
    body: form,
  });
  if (!up.ok) return console.error('上传失败', up.status, await up.text());

  console.log('%c喵链接：', 'color:#0a0;font-weight:bold', url);
  try {
    await navigator.clipboard.writeText(url);
    console.log('（已复制到剪贴板）');
  } catch (e) {
    console.log('（复制失败，手动选中上面的网址）');
  }
  return url;
})();
