// Settles a galaxy's field off the main thread (field.ts is O(n²) per step).
import { layoutField, type FieldPaper } from './field';

self.onmessage = (e: MessageEvent<{ id: number; papers: FieldPaper[]; citations: [string, string][] }>) => {
  const { id, papers, citations } = e.data;
  self.postMessage({ id, field: layoutField(papers, citations) });
};
