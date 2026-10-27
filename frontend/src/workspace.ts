// The workspace a page reads. Each account works in its own; an administrator
// may read another account's with `?owner=<id>` (the server ignores it for
// everyone else), which pages pass on with `withOwner`.
import { useSearchParams } from 'react-router';
import { useSignedIn } from './session';

export function useOwner(): string | null {
  const session = useSignedIn();
  const [params] = useSearchParams();
  const owner = params.get('owner');
  return session.user.role === 'admin' && owner && owner !== session.user.id ? owner : null;
}
