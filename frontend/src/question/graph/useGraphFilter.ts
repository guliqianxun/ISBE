// The graph filter as the run page holds it (#55): one for the graph card and the
// star map. A rebuild may number the communities anew, so the hidden ones start
// afresh when the graph's fingerprint changes; the rest of the filter stays.
import { useState } from 'react';
import { NO_FILTER, type Filter } from './model';

export function useGraphFilter(fingerprint: string | undefined): [Filter, (f: Filter) => void] {
  const [filter, setFilter] = useState<Filter>(NO_FILTER);
  const [filteredFor, setFilteredFor] = useState(fingerprint);
  if (filteredFor !== fingerprint) {
    setFilteredFor(fingerprint);
    if (filter.hidden.length) setFilter({ ...filter, hidden: [] });
  }
  return [filter, setFilter];
}
