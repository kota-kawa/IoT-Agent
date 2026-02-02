import { useEffect, useState, type FormEvent } from 'react';
import { useLocation, useNavigate } from 'react-router-dom';
import { fetchJson } from '../api';
import type { SessionResponse } from '../types';

export default function Login(): JSX.Element {
  const navigate = useNavigate();
  const location = useLocation();
  const [password, setPassword] = useState('');
  const [error, setError] = useState(false);
  const [submitting, setSubmitting] = useState(false);

  useEffect(() => {
    const params = new URLSearchParams(location.search);
    if (params.get('error') === '1') {
      setError(true);
    }
  }, [location.search]);

  useEffect(() => {
    let active = true;
    const checkSession = async () => {
      try {
        const { response, data } = await fetchJson<SessionResponse>('/api/session');
        if (!active) return;
        if (response.ok && data?.authenticated) {
          navigate('/', { replace: true });
        }
      } catch {
        if (!active) return;
      }
    };
    checkSession();
    return () => {
      active = false;
    };
  }, [navigate]);

  const handleSubmit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!password || submitting) return;
    setSubmitting(true);
    setError(false);

    try {
      const { response, data } = await fetchJson<SessionResponse>('/api/session', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ password })
      });

      if (response.ok && data?.authenticated) {
        navigate('/', { replace: true });
        return;
      }
      setError(true);
    } catch {
      setError(true);
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className="login-page">
      <div className="login-container">
        <h1>ログインが必要です</h1>
        {error && <p className="error">パスワードが正しくありません。</p>}
        <form onSubmit={handleSubmit}>
          <label htmlFor="password">パスワード</label>
          <input
            type="password"
            id="password"
            name="password"
            value={password}
            onChange={(event) => setPassword(event.target.value)}
            autoComplete="current-password"
            required
          />
          <button type="submit" disabled={submitting}>
            {submitting ? 'ログイン中…' : 'ログイン'}
          </button>
        </form>
      </div>
    </div>
  );
}
