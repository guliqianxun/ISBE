// Community colours, one set for the graph card and the star map so a community
// reads the same in both: Tableau 10, which Zhiheng approved in the prototype. Each
// keeps at least 3 : 1 against the map's night sky (star-map.md section 2).
const PALETTE = [
  '#4e79a7',
  '#f28e2b',
  '#59a14f',
  '#e15759',
  '#b07aa1',
  '#76b7b2',
  '#edc948',
  '#ff9da7',
  '#9c755f',
  '#bab0ac',
];
const NONE = '#9a9a9a';
export const colour = (c: number | null | undefined): string =>
  c === null || c === undefined ? NONE : PALETTE[c % 10]!;
