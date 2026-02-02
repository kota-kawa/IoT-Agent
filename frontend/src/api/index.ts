import type { AppConfig } from '../types';

export type FetchJsonResult<T> = {
  response: Response;
  data: T | null;
  text: string;
};

const appConfig: AppConfig = typeof window !== 'undefined' && window.__APP_CONFIG__
  ? window.__APP_CONFIG__
  : {};
const rawBase = typeof appConfig.apiBase === 'string' ? appConfig.apiBase.trim() : '';
const API_BASE = rawBase ? rawBase.replace(/\/$/, '') : '';

export const apiUrl = (path: string): string => {
  if (!path) return API_BASE || '';
  if (path.startsWith('/')) {
    return `${API_BASE}${path}`;
  }
  return `${API_BASE}/${path}`;
};

export async function fetchJson<T = unknown>(
  path: string,
  options: RequestInit = {}
): Promise<FetchJsonResult<T>> {
  const response = await fetch(apiUrl(path), {
    credentials: 'include',
    cache: 'no-store',
    ...options
  });

  const text = await response.text();
  let data: T | null = null;
  if (text) {
    try {
      data = JSON.parse(text) as T;
    } catch {
      data = null;
    }
  }

  return { response, data, text };
}
