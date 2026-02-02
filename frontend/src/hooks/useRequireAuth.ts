import { useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { fetchJson } from '../api';
import type { SessionResponse } from '../types';

export function useRequireAuth(): boolean {
  const navigate = useNavigate();
  const [ready, setReady] = useState(false);

  useEffect(() => {
    let active = true;
    const check = async () => {
      try {
        const { response, data } = await fetchJson<SessionResponse>('/api/session');
        if (!active) return;
        if (response.ok && data?.authenticated) {
          setReady(true);
        } else {
          navigate('/login', { replace: true });
        }
      } catch {
        if (!active) return;
        navigate('/login', { replace: true });
      }
    };
    check();
    return () => {
      active = false;
    };
  }, [navigate]);

  return ready;
}
