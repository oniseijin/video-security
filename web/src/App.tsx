import { Route, Routes } from "react-router-dom"
import { Breadcrumbs } from "./components/Breadcrumbs"
import { Masthead } from "./components/Masthead"
import { Nav } from "./components/Nav"
import { Dashboard } from "./routes/Dashboard"
import { JobsList } from "./routes/JobsList"
import { NotFound } from "./routes/NotFound"
import { Placeholder } from "./routes/Placeholder"

export function App() {
  return (
    <div className="app-shell">
      <Masthead />
      <Nav />
      <Breadcrumbs />
      <main className="app-main">
        <Routes>
          <Route path="/" element={<Dashboard />} />
          <Route path="/jobs" element={<JobsList />} />
          <Route path="/events" element={<Placeholder label="Events" phase={7} />} />
          <Route path="/plates" element={<Placeholder label="Plates" phase={9} />} />
          <Route path="/search" element={<Placeholder label="Search" phase={9} />} />
          <Route path="*" element={<NotFound />} />
        </Routes>
      </main>
    </div>
  )
}
