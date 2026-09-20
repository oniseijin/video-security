export function fmtDate(value: string | null | undefined): string {
  if (!value) {
    return "—"
  }
  return value.slice(0, 16).replace("T", " ")
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
