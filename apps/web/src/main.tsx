import React from "react";
import ReactDOM from "react-dom/client";
import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom";
import "./styles.css";
import Layout from "@/components/Layout";
import Login from "@/app/login/page";
import Onboarding from "@/app/onboarding/page";
import Inbox from "@/app/inbox/page";
import JobPage from "@/app/jobs/[id]/page";
import Studio from "@/app/studio/[id]/page";
import Pipeline from "@/app/pipeline/page";
import Messages from "@/app/messages/page";
import RunPage from "@/app/runs/[id]/page";
import AnalyticsPage from "@/app/analytics/page";
import SettingsPage from "@/app/settings/page";

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <BrowserRouter>
      <Routes>
        <Route path="/login" element={<Login />} />
        <Route element={<Layout />}>
          <Route path="/onboarding" element={<Onboarding />} />
          <Route path="/inbox" element={<Inbox />} />
          <Route path="/jobs/:id" element={<JobPage />} />
          <Route path="/studio/:id" element={<Studio />} />
          <Route path="/pipeline" element={<Pipeline />} />
          <Route path="/messages" element={<Messages />} />
          <Route path="/runs/:id" element={<RunPage />} />
          <Route path="/analytics" element={<AnalyticsPage />} />
          <Route path="/settings" element={<SettingsPage />} />
        </Route>
        <Route path="*" element={<Navigate to="/inbox" replace />} />
      </Routes>
    </BrowserRouter>
  </React.StrictMode>,
);
