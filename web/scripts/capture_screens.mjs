import { chromium } from "playwright"
import { spawn } from "node:child_process"
import { existsSync, mkdirSync } from "node:fs"
import net from "node:net"
import path from "node:path"
import { fileURLToPath } from "node:url"

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../..")
const DEFAULT_CONFIG = path.join(
  process.env.HOME ?? "",
  ".local/opt/video-security/var/config.toml"
)
const VIEWPORT = { width: 1440, height: 900 }
const REPORT_SECTIONS = [
  "Summary",
  "Timeline",
  "Driving Log",
  "Keyframes",
  "Location Track",
  "Plates",
  "Transcript",
  "Watchlist",
]

const sleep = (ms) => new Promise((r) => setTimeout(r, ms))

function parseArgs(argv) {
  const opts = {
    job: null,
    config: DEFAULT_CONFIG,
    out: path.join(ROOT, "docs/images"),
    baseUrl: null,
    only: null,
    themes: ["machine", "samaritan"],
  }
  for (let i = 0; i < argv.length; i++) {
    const a = argv[i]
    if (a === "--job") opts.job = Number(argv[++i])
    else if (a === "--config") opts.config = argv[++i]
    else if (a === "--out") opts.out = argv[++i]
    else if (a === "--base-url") opts.baseUrl = argv[++i]
    else if (a === "--only") opts.only = argv[++i]
    else if (a === "--themes") opts.themes = argv[++i].split(",")
    else throw new Error(`unknown arg: ${a}`)
  }
  return opts
}

function freePort() {
  return new Promise((resolve, reject) => {
    const srv = net.createServer()
    srv.listen(0, "127.0.0.1", () => {
      const port = srv.address().port
      srv.close(() => resolve(port))
    })
    srv.on("error", reject)
  })
}

async function startServer(opts) {
  const vs = path.join(ROOT, ".venv/bin/vs")
  if (!existsSync(vs)) throw new Error(`${vs} not found`)
  const port = await freePort()
  const child = spawn(
    vs,
    ["--config", opts.config, "serve", "--port", String(port)],
    { stdio: ["ignore", "pipe", "pipe"] }
  )
  const base = `http://127.0.0.1:${port}`
  const deadline = Date.now() + 30000
  while (Date.now() < deadline) {
    try {
      const r = await fetch(`${base}/api/health`)
      if (r.ok) return { child, base }
    } catch {}
    await sleep(250)
  }
  child.kill()
  throw new Error("vs serve did not become healthy")
}

async function getJson(url) {
  const r = await fetch(url)
  if (!r.ok) throw new Error(`GET ${url} -> ${r.status}`)
  return r.json()
}

async function pickJob(base, preferred) {
  if (preferred) return preferred
  const jobs = (await getJson(`${base}/api/jobs?limit=1000`)).items
  const score = (j) =>
    j.counts.events +
    3 * j.counts.plates +
    2 * j.counts.faces +
    (j.has_gps ? 5 : 0) +
    (j.has_transcript ? 3 : 0) +
    (j.pair_job_id ? 4 : 0)
  return jobs.reduce((a, b) => (score(b) > score(a) ? b : a)).id
}

async function main() {
  const opts = parseArgs(process.argv.slice(2))
  mkdirSync(opts.out, { recursive: true })
  const spawned = opts.baseUrl ? null : await startServer(opts)
  const base = opts.baseUrl ?? spawned.base
  try {
    const jobId = await pickJob(base, opts.job)
    const jobEvents = (await getJson(`${base}/api/jobs/${jobId}/events`)).items
    let event = jobEvents.find((e) => !e.event_type.startsWith("audio"))
    if (!event) event = jobEvents[0]
    for (const e of jobEvents) {
      const detail = await getJson(`${base}/api/events/${e.event_id}`)
      if (detail.keyframes?.length && !e.event_type.startsWith("audio")) {
        event = e
        break
      }
    }
    const allPlates = (await getJson(`${base}/api/plates`)).items
    const topPlate = allPlates[0]
    const jobTracks = (await getJson(`${base}/api/jobs/${jobId}/tracks`)).items
    const track =
      jobTracks.find((t) => t.plate_norm) ?? jobTracks.find((t) => t.strip) ?? jobTracks[0]
    const facesCount = (await getJson(`${base}/api/faces`)).items?.length ?? 0
    const persons = (await getJson(`${base}/api/persons`)).items ?? []

    const jobPath = `/jobs/${jobId}`
    const shots = [
      { name: "web-dashboard", url: "/" },
      { name: "web-jobs", url: "/jobs" },
      { name: "web-events", url: "/events" },
      { name: "web-timeline", url: "/timeline" },
      { name: "web-plates", url: "/plates" },
      topPlate && {
        name: "web-plate-detail",
        url: `/plates/${encodeURIComponent(topPlate.norm_text)}`,
      },
      { name: "web-search", url: `/search?q=${encodeURIComponent(topPlate?.norm_text ?? "(event)")}` },
      event && { name: "web-event-detail", url: `${jobPath}/events/${event.event_id}` },
      track && { name: "web-track-detail", url: `${jobPath}/tracks/${track.track_id}` },
      facesCount > 0 && { name: "web-faces", url: "/faces" },
      persons.length > 0 && { name: "web-persons", url: "/persons" },
      { name: "web-job-captures", url: `${jobPath}/captures` },
      { name: "web-job-events", url: `${jobPath}/events` },
      { name: "web-job-map", url: `${jobPath}/map`, settleMs: 3000 },
      { name: "web-job-plates", url: `${jobPath}/plates` },
      { name: "web-job-playback", url: `${jobPath}/playback`, settleMs: 3000 },
      { name: "web-job-transcript", url: `${jobPath}/transcript` },
      { name: "web-job-report", url: `${jobPath}/report`, settleMs: 3000 },
      {
        name: "web-lightbox",
        url: event ? `${jobPath}/events/${event.event_id}` : `${jobPath}/captures`,
        action: async (page) => {
          await page.locator(".kf-wrap img").first().click()
          await page.locator(".lightbox.open").waitFor({ timeout: 5000 })
          await sleep(800)
        },
      },
      { name: "report-top", url: `/api/jobs/${jobId}/report.html`, report: true },
      { name: "report-full", url: `/api/jobs/${jobId}/report.html`, report: true, fullPage: true },
    ].filter(Boolean)

    for (const section of REPORT_SECTIONS) {
      shots.push({
        name: `report-${section.toLowerCase().replace(/\s+/g, "-")}`,
        url: `/api/jobs/${jobId}/report.html`,
        report: true,
        section,
      })
    }

    const browser = await chromium.launch()
    const captured = []
    const skipped = []
    try {
      for (const theme of opts.themes) {
        const context = await browser.newContext({
          viewport: VIEWPORT,
          deviceScaleFactor: 2,
        })
        await context.addInitScript((t) => {
          try {
            localStorage.setItem("vs-theme", t)
            localStorage.setItem("vs-faces", "on")
          } catch {}
        }, theme)
        const page = await context.newPage()
        page.setDefaultTimeout(30000)
        for (const shot of shots) {
          const file = `${shot.name}-${theme}.png`
          if (opts.only && !file.includes(opts.only)) continue
          const target = path.join(opts.out, file)
          try {
            await page.goto(base + shot.url, { waitUntil: "networkidle" })
            await sleep(shot.settleMs ?? 500)
            if (shot.action) await shot.action(page)
            if (shot.section) {
              const el = page
                .locator("section.panel")
                .filter({
                  has: page.getByRole("heading", { name: shot.section, exact: true }),
                })
                .first()
              if ((await el.count()) === 0) {
                skipped.push(file)
                continue
              }
              await el.scrollIntoViewIfNeeded()
              await sleep(300)
              await el.screenshot({ path: target })
            } else if (shot.fullPage) {
              const height = await page.evaluate(
                () => document.documentElement.scrollHeight
              )
              await page.screenshot({
                path: target,
                fullPage: true,
                clip: { x: 0, y: 0, width: VIEWPORT.width, height: Math.min(height, 12000) },
              })
            } else {
              await page.screenshot({ path: target })
            }
            captured.push(file)
          } catch (err) {
            skipped.push(`${file} (${err.message.split("\n")[0]})`)
          }
        }
        await context.close()
      }
    } finally {
      await browser.close()
    }
    console.log(`job ${jobId}: captured ${captured.length} -> ${opts.out}`)
    for (const f of captured) console.log(`  ok   ${f}`)
    for (const f of skipped) console.log(`  skip ${f}`)
  } finally {
    if (spawned) spawned.child.kill()
  }
}

main().catch((err) => {
  console.error(err)
  process.exit(1)
})
