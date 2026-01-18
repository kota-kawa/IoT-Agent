import { useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { fetchJson } from './api.js';

export function useRequireAuth() {
  const navigate = useNavigate();
  const [ready, setReady] = useState(false);

  useEffect(() => {
    let active = true;
    const check = async () => {
      try {
        const { response, data } = await fetchJson('/api/session');
        if (!active) return;
        if (response.ok && data?.authenticated) {
          setReady(true);
        } else {
          navigate('/login', { replace: true });
        }
      } catch (_err) {
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
