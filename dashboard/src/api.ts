export async function api<T = any>(path: string, options?: RequestInit): Promise<T> {
  const response = await fetch('/api' + path, { ...options, headers: { 'Content-Type': 'application/json', ...options?.headers } });
  const result = await response.json();
  if (!response.ok) throw new Error(typeof result.detail === 'string' ? result.detail : JSON.stringify(result.detail));
  return result;
}
export const post = <T = any>(path: string, data: any = {}) => api<T>(path, {method: 'POST', body: JSON.stringify(data)});
export const number = (value: number | null | undefined, digits = 0) => value == null ? '--' : value.toLocaleString('en-GB', {maximumFractionDigits: digits});
