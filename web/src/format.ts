export function fmtDate(value: string | null | undefined): string {
  if (!value) {
    return "—"
  }
  return value.slice(0, 16).replace("T", " ")
}

export function fmtSec(value: number | null | undefined): string {
  if (value === null || value === undefined || Number.isNaN(value)) {
    return "—"
  }
  const total = Math.max(0, Math.round(value))
  const h = Math.floor(total / 3600)
  const m = Math.floor((total % 3600) / 60)
  const s = total % 60
  const mm = String(m).padStart(2, "0")
  const ss = String(s).padStart(2, "0")
  return h > 0 ? `${h}:${mm}:${ss}` : `${mm}:${ss}`
}

export function fmtBytes(value: number): string {
  if (value <= 0) {
    return "0 B"
  }
  const units = ["B", "KB", "MB", "GB", "TB"]
  let size = value
  let unit = 0
  while (size >= 1024 && unit < units.length - 1) {
    size /= 1024
    unit += 1
  }
  const rounded = unit === 0 || size >= 10 ? Math.round(size) : Number(size.toFixed(1))
  return `${rounded} ${units[unit]}`
}
