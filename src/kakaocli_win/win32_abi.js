// Win32 structures are laid out for the target process, not the Node/Python host.
function win32Layout(pointerSize) {
  if (pointerSize === 4) return {
    abi: 'stdcall',
    getTextEx: {size: 20, defaultChar: 12, usedDefaultChar: 16},
    openFileName: {minSize: 76, owner: 4, file: 28, maxFile: 32,
      fileTitle: 36, maxFileTitle: 40, fileOffset: 56, fileExtension: 58,
      defaultExtension: 60},
  };
  if (pointerSize === 8) return {
    abi: 'win64',
    getTextEx: {size: 32, defaultChar: 16, usedDefaultChar: 24},
    openFileName: {minSize: 136, owner: 8, file: 48, maxFile: 56,
      fileTitle: 64, maxFileTitle: 72, fileOffset: 100, fileExtension: 102,
      defaultExtension: 104},
  };
  throw Error('Unsupported Windows pointer size: ' + pointerSize);
}

function mapCapturePoint(point, sourceSize, targetSize) {
  if (!Array.isArray(point) || point.length !== 2 ||
      ![point, sourceSize, targetSize].every(a => Array.isArray(a) && a.length === 2 && a.every(Number.isFinite)) ||
      !sourceSize.every(v => Number.isInteger(v) && v > 0) || !targetSize.every(v => Number.isInteger(v) && v > 0) ||
      point.some((v, i) => v < 0 || v >= sourceSize[i])) {
    throw Error('Invalid capture point or geometry');
  }
  return point.map((v, i) => Math.min(targetSize[i] - 1, Math.round(v * targetSize[i] / sourceSize[i])));
}

function isInputCue(text, defaultFormat, selectionFormat) {
  // REA-observed Korean cue in KakaoTalk 26.5 x86. The literal alone is never
  // sufficient: user-entered text uses the default automatic black color.
  const colorMask = 0x40000000;
  const formats = [defaultFormat, selectionFormat];
  if (text !== '메시지 입력' || !formats.every(f => f &&
      ['mask', 'effects', 'color'].every(k => Number.isInteger(f[k])) &&
      (f.mask & colorMask) !== 0)) return false;
  return (defaultFormat.effects & colorMask) !== 0 && defaultFormat.color === 0 &&
    (selectionFormat.effects & colorMask) === 0 && selectionFormat.color === 0x949494;
}
