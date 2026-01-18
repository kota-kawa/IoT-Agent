const API_BASE = window.location.protocol === 'file:' ? 'http://localhost:5006' : '';

export const apiUrl = (path) => {
  if (!path) return API_BASE || '';
  if (path.startsWith('/')) {
    return `${API_BASE}${path}`;
  }
  return `${API_BASE}/${path}`;
};

export async function fetchJson(path, options = {}) {
  const response = await fetch(apiUrl(path), {
    credentials: 'include',
    cache: 'no-store',
    ...options
  });

  const text = await response.text();
  let data = null;
  if (text) {
    try {
      data = JSON.parse(text);
    } catch (_err) {
      data = null;
    }
  }

  return { response, data, text };
}
