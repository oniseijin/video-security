import { Route, Routes } from "react-router-dom"
import { Breadcrumbs, CrumbProvider } from "./components/Breadcrumbs"
import { Masthead } from "./components/Masthead"
import { Nav } from "./components/Nav"
import { Dashboard } from "./routes/Dashboard"
import { JobDetail } from "./routes/JobDetail"
import { JobsList } from "./routes/JobsList"
import { NotFound } from "./routes/NotFound"
import { Placeholder } from "./routes/Placeholder"

export function App() {
  return (
    <CrumbProvider>
      <div className="app-shell">
        <Masthead />
        <Nav />
        <Breadcrumbs />
        <main className="app-main">
          <Routes>
            <Route path="/" element={<Dashboard />} />
            <Route path="/jobs" element={<JobsList />} />
            <Route path="/jobs/:id/:tab?" element={<JobDetail />} />
            <Route path="/events" element={<Placeholder label="Events" phase={7} />} />
            <Route path="/plates" element={<Placeholder label="Plates" phase={9} />} />
            <Route path="/search" element={<Placeholder label="Search" phase={9} />} />
            <Route path="*" element={<NotFound />} />
          </Routes>
        </main>
      </div>
    </CrumbProvider>
  )
}
