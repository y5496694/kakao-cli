// Read-only inspection: no hooks, input events, message sends or memory writes.
const main = Process.mainModule;
send({kind: 'module', name: main.name, base: String(main.base), size: main.size, arch: Process.arch});
const patterns = ['EVA_Window_Dblclk', 'RICHEDIT50W', 'ChatRoomListView', 'ContactListView', 'ChatLogList', 'sqlite3_key'];
const sections = Process.enumerateRanges('r--').filter(r => r.base.compare(main.base) >= 0 && r.base.compare(main.base.add(main.size)) < 0);
for (const label of patterns) {
  const bytes = Array.from(label).flatMap(c => [c.charCodeAt(0), 0]);
  const pattern = bytes.map(b => b.toString(16).padStart(2, '0')).join(' ');
  const matches = [];
  for (const range of sections) {
    try { for (const m of Memory.scanSync(range.base, range.size, pattern)) matches.push(String(m.address.sub(main.base))); } catch (e) {}
  }
  send({kind: 'readonly_string', label, rvas: matches.slice(0, 10), count: matches.length});
}
const user32 = Process.getModuleByName('user32.dll');
const getPid = new NativeFunction(user32.getExportByName('GetWindowThreadProcessId'), 'uint32', ['pointer', 'pointer']);
const getClass = new NativeFunction(user32.getExportByName('GetClassNameW'), 'int', ['pointer', 'pointer', 'int']);
const getId = new NativeFunction(user32.getExportByName('GetDlgCtrlID'), 'int', ['pointer']);
const isVisible = new NativeFunction(user32.getExportByName('IsWindowVisible'), 'int', ['pointer']);
const enumChildren = new NativeFunction(user32.getExportByName('EnumChildWindows'), 'int', ['pointer', 'pointer', 'pointer']);
const enumWindows = new NativeFunction(user32.getExportByName('EnumWindows'), 'int', ['pointer', 'pointer']);
const controls = [];
const scratch = Memory.alloc(1024);
const pid = Memory.alloc(4);
const childCallback = new NativeCallback((hwnd, _) => {
  getClass(hwnd, scratch, 512);
  controls.push({hwnd: String(hwnd), class_name: scratch.readUtf16String(), control_id: getId(hwnd), visible: !!isVisible(hwnd)});
  return 1;
}, 'int', ['pointer', 'pointer']);
const topCallback = new NativeCallback((hwnd, _) => {
  getPid(hwnd, pid);
  if (pid.readU32() === Process.id) enumChildren(hwnd, childCallback, ptr(0));
  return 1;
}, 'int', ['pointer', 'pointer']);
enumWindows(topCallback, ptr(0));
send({kind: 'controls', controls});
