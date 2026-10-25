// ui/layout: pad index <-> 4x4 grid position. Pad 0 is bottom-left (MPC convention).
export function padToCell(pad) {
  const row = 3 - Math.floor(pad / 4);
  const col = pad % 4;
  return { row, col };
}

export function cellToPad(row, col) {
  return (3 - row) * 4 + col;
}
