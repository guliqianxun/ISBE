// The map's search (#55): the shown nodes whose paper title or label holds the
// words; asking again moves on to the next one, round to the first.
export function findNext(
  nodes: Iterable<[string, { label?: string | null }]>,
  titleOf: (key: string) => string | undefined,
  text: string,
  after: string | null,
): string | null {
  const words = text.trim().toLowerCase();
  if (!words) return null;
  const hits: string[] = [];
  for (const [key, n] of nodes)
    if (`${titleOf(key) ?? ''} ${n.label ?? ''}`.toLowerCase().includes(words)) hits.push(key);
  if (!hits.length) return null;
  const at = after === null ? -1 : hits.indexOf(after);
  return hits[(at + 1) % hits.length]!;
}
