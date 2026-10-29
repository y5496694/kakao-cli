import fs from 'node:fs';
import {createRequire} from 'node:module';
import {pathToFileURL} from 'node:url';

const request = JSON.parse(fs.readFileSync(0, 'utf8'));
const require = createRequire(import.meta.url);
let session, script, result, error, cleanup_error;
try {
  const modulePath = require.resolve('frida', {paths: [process.argv[2] || process.cwd()]});
  const frida = await import(pathToFileURL(modulePath).href);
  session = await frida.attach(request.pid);
  const source = fs.readFileSync(new URL('./background_agent.js', import.meta.url), 'utf8');
  script = await session.createScript('const config = '+JSON.stringify(request)+';\n'+source);
  let timer;
  const completion = new Promise(resolve => {
    timer = setTimeout(()=>resolve({ok:false,error:'Background operation deadline exceeded; do not retry a send without checking its outcome.'}), request.timeout * 1000);
    script.message.connect(message => {
      if (message.type === 'error') resolve({ok:false,error:message.description});
      if (message.type === 'send' && message.payload?.event === 'done') resolve(message.payload);
    });
    session.detached.connect(reason=>resolve({ok:false,error:'KakaoTalk instrumentation detached: '+reason}));
  });
  try { await script.load(); result = await completion; } finally { clearTimeout(timer); }
} catch (failure) { error = failure.message; }
finally {
  try { if (script) await script.unload(); } catch (failure) { cleanup_error = failure.message; }
  try { if (session) await session.detach(); } catch (failure) { cleanup_error = [cleanup_error, failure.message].filter(Boolean).join('; '); }
}
process.stdout.write(JSON.stringify({ok: !error && result?.ok === true, result: result?.result, error: error || result?.error, cleanup_error})+'\n');
process.exitCode = error || cleanup_error || result?.ok !== true ? 1 : 0;
