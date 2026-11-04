// Read-only inspection: no hooks, input events, message sends or app-state writes.
// Window titles and control text are deliberately never read or emitted.
if (Process.platform !== 'windows' || ![4, 8].includes(Process.pointerSize)) {
  throw Error('This observer supports Windows x86/x64 targets only');
}
const abi = Process.pointerSize === 4 ? 'stdcall' : 'win64';
const user32 = Process.getModuleByName('user32.dll');
const api = (name, result, args) => new NativeFunction(user32.getExportByName(name), result, args, abi);
const optionalApi = (name, result, args) => {
  const address = user32.findExportByName(name);
  return address ? new NativeFunction(address, result, args, abi) : null;
};
const getThreadDpiContext = optionalApi('GetThreadDpiAwarenessContext', 'pointer', []);
const getDpiAwareness = optionalApi('GetAwarenessFromDpiAwarenessContext', 'int', ['pointer']);
const threadDpiContext = getThreadDpiContext ? getThreadDpiContext() : null;
const main = Process.mainModule;
send({kind: 'module', name: main.name, base: String(main.base), size: main.size,
  arch: Process.arch, platform: Process.platform, pointer_size: Process.pointerSize, abi,
  observer_thread_dpi_awareness_context: threadDpiContext ? String(threadDpiContext) : null,
  observer_thread_dpi_awareness: threadDpiContext && getDpiAwareness ? getDpiAwareness(threadDpiContext) : null});
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
const getPid = api('GetWindowThreadProcessId', 'uint32', ['pointer', 'pointer']);
const getClass = api('GetClassNameW', 'int', ['pointer', 'pointer', 'int']);
const getId = api('GetDlgCtrlID', 'int', ['pointer']);
const isVisible = api('IsWindowVisible', 'int', ['pointer']);
const enumChildren = api('EnumChildWindows', 'int', ['pointer', 'pointer', 'pointer']);
const enumWindows = api('EnumWindows', 'int', ['pointer', 'pointer']);
const getClientRect = api('GetClientRect', 'int', ['pointer', 'pointer']);
const getWindowRect = api('GetWindowRect', 'int', ['pointer', 'pointer']);
const getDpi = optionalApi('GetDpiForWindow', 'uint32', ['pointer']);
const getWindowDpiContext = optionalApi('GetWindowDpiAwarenessContext', 'pointer', ['pointer']);
const readRect = (fn, hwnd) => {
  const rect = Memory.alloc(16);
  return fn(hwnd, rect) ? [0, 4, 8, 12].map(offset => rect.add(offset).readS32()) : null;
};
const controls = [];
const topLevelWindows = [];
const scratch = Memory.alloc(1024);
const pid = Memory.alloc(4);
const childCallback = new NativeCallback((hwnd, _) => {
  getClass(hwnd, scratch, 512);
  controls.push({hwnd: String(hwnd), class_name: scratch.readUtf16String(), control_id: getId(hwnd), visible: !!isVisible(hwnd)});
  return 1;
}, 'int', ['pointer', 'pointer'], abi);
const topCallback = new NativeCallback((hwnd, _) => {
  getPid(hwnd, pid);
  if (pid.readU32() !== Process.id) return 1;
  getClass(hwnd, scratch, 512);
  const className = scratch.readUtf16String();
  if (className === 'EVA_Window_Dblclk') {
    const windowDpiContext = getWindowDpiContext ? getWindowDpiContext(hwnd) : null;
    topLevelWindows.push({hwnd: String(hwnd), class_name: className,
      visible: !!isVisible(hwnd), client_rect: readRect(getClientRect, hwnd),
      window_rect: readRect(getWindowRect, hwnd), dpi: getDpi ? getDpi(hwnd) : null,
      dpi_awareness_context: windowDpiContext ? String(windowDpiContext) : null,
      dpi_awareness: windowDpiContext && getDpiAwareness ? getDpiAwareness(windowDpiContext) : null});
  }
  enumChildren(hwnd, childCallback, ptr(0));
  return 1;
}, 'int', ['pointer', 'pointer'], abi);
enumWindows(topCallback, ptr(0));
send({kind: 'controls', controls});
send({kind: 'window_geometry', windows: topLevelWindows});
