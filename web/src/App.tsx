import { Route, Routes } from "react-router-dom"
import { Breadcrumbs, CrumbProvider } from "./components/Breadcrumbs"
import { Masthead } from "./components/Masthead"
import { Nav } from "./components/Nav"
import { Dashboard } from "./routes/Dashboard"
import { EventDetailRoute } from "./routes/EventDetail"
import { Events } from "./routes/Events"
import { Faces } from "./routes/Faces"
import { JobDetail } from "./routes/JobDetail"
import { JobsList } from "./routes/JobsList"
import { NotFound } from "./routes/NotFound"
import { PersonDetailRoute } from "./routes/PersonDetail"
import { Persons } from "./routes/Persons"
import { PlateDetailRoute } from "./routes/PlateDetail"
import { Plates } from "./routes/Plates"
import { Search } from "./routes/Search"
import { TrackDetailRoute } from "./routes/TrackDetail"

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
            <Route path="/jobs/:id/events/:eid" element={<EventDetailRoute />} />
            <Route path="/jobs/:id/tracks/:tid" element={<TrackDetailRoute />} />
            <Route path="/events" element={<Events />} />
            <Route path="/plates" element={<Plates />} />
            <Route path="/plates/:norm" element={<PlateDetailRoute />} />
            <Route path="/faces" element={<Faces />} />
            <Route path="/persons" element={<Persons />} />
            <Route path="/persons/:id" element={<PersonDetailRoute />} />
            <Route path="/search" element={<Search />} />
            <Route path="*" element={<NotFound />} />
          </Routes>
        </main>
      </div>
    </CrumbProvider>
  )
}
