import { Center, Loader } from "@mantine/core";
import { lazy, Suspense, type ComponentType } from "react";
import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom";
import { ConfigProvider } from "./config";
import { AppShell } from "./layout/AppShell";
import { Login } from "./pages/Login";
import { SessionProvider, useSession } from "./session";
import { StatusProvider } from "./status";
import { SetupWizardProvider } from "./wizard/SetupWizard";

function lazyPage(loader: () => Promise<{ [key: string]: ComponentType }>, name: string) {
  return lazy(async () => {
    const mod = await loader();
    return { default: mod[name] as ComponentType };
  });
}

const Dashboard = lazyPage(() => import("./pages/Dashboard"), "Dashboard");
const Activity = lazyPage(() => import("./pages/Activity"), "Activity");
const Timing = lazyPage(() => import("./pages/Timing"), "Timing");
const Identification = lazyPage(() => import("./pages/Identification"), "Identification");
const Audio = lazyPage(() => import("./pages/Audio"), "Audio");
const Macros = lazyPage(() => import("./pages/Macros"), "Macros");
const Net = lazyPage(() => import("./pages/Net"), "Net");
const Mailbox = lazyPage(() => import("./pages/Mailbox"), "Mailbox");
const Announcements = lazyPage(() => import("./pages/Announcements"), "Announcements");
const Weather = lazyPage(() => import("./pages/Weather"), "Weather");
const Autopatch = lazyPage(() => import("./pages/Autopatch"), "Autopatch");
const AllStar = lazyPage(() => import("./pages/AllStar"), "AllStar");
const Aprs = lazyPage(() => import("./pages/Aprs"), "Aprs");
const MapPage = lazyPage(() => import("./pages/MapPage"), "MapPage");
const PublicListening = lazyPage(() => import("./pages/PublicListening"), "PublicListening");
const Simulator = lazyPage(() => import("./pages/Simulator"), "Simulator");
const Logs = lazyPage(() => import("./pages/Logs"), "Logs");
const Backup = lazyPage(() => import("./pages/Backup"), "Backup");
const Users = lazyPage(() => import("./pages/Users"), "Users");
const Audit = lazyPage(() => import("./pages/Audit"), "Audit");
const Alerts = lazyPage(() => import("./pages/Alerts"), "Alerts");
const Updates = lazyPage(() => import("./pages/Updates"), "Updates");
const Remote = lazyPage(() => import("./pages/Remote"), "Remote");
const Listen = lazyPage(() => import("./pages/Listen"), "Listen");

function PageFallback() {
  return (
    <Center mih="40vh">
      <Loader color="accent" />
    </Center>
  );
}

function AuthedApp() {
  const { loading, session } = useSession();
  if (loading || !session) {
    return (
      <Center mih="100dvh">
        <Loader color="accent" />
      </Center>
    );
  }
  return (
    <ConfigProvider>
      <StatusProvider>
        <SetupWizardProvider>
          <Suspense fallback={<PageFallback />}>
            <Routes>
              <Route element={<AppShell />}>
                <Route index element={<Navigate to="/dashboard" replace />} />
                <Route path="dashboard" element={<Dashboard />} />
                <Route path="activity" element={<Activity />} />
                <Route path="timing" element={<Timing />} />
                <Route path="identification" element={<Identification />} />
                <Route path="audio" element={<Audio />} />
                <Route path="macros" element={<Macros />} />
                <Route path="net" element={<Net />} />
                <Route path="mailbox" element={<Mailbox />} />
                <Route path="announcements" element={<Announcements />} />
                <Route path="weather" element={<Weather />} />
                <Route path="autopatch" element={<Autopatch />} />
                <Route path="allstar" element={<AllStar />} />
                <Route path="aprs" element={<Aprs />} />
                <Route path="map" element={<MapPage />} />
                <Route path="public" element={<PublicListening />} />
                <Route path="simulator" element={<Simulator />} />
                <Route path="logs" element={<Logs />} />
                <Route path="backup" element={<Backup />} />
                <Route path="users" element={<Users />} />
                <Route path="audit" element={<Audit />} />
                <Route path="alerts" element={<Alerts />} />
                <Route path="updates" element={<Updates />} />
                <Route path="remote" element={<Remote />} />
                <Route path="*" element={<Navigate to="/dashboard" replace />} />
              </Route>
            </Routes>
          </Suspense>
        </SetupWizardProvider>
      </StatusProvider>
    </ConfigProvider>
  );
}

export default function App() {
  return (
    <BrowserRouter>
      <Routes>
        <Route path="/login" element={<Login />} />
        <Route
          path="/listen"
          element={
            <Suspense fallback={<PageFallback />}>
              <Listen />
            </Suspense>
          }
        />
        <Route
          path="/*"
          element={
            <SessionProvider>
              <AuthedApp />
            </SessionProvider>
          }
        />
      </Routes>
    </BrowserRouter>
  );
}
