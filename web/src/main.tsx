import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter } from "react-router-dom";
import App from "./App.tsx";
import { AuthProvider } from "@/auth/AuthContext";
import { ToastProvider } from "@/auth/ToastContext";
import { JobsProvider } from "@/jobs/JobsContext";
import { UploadsProvider } from "@/uploads/UploadsContext";
import { UploadsDock } from "@/components/UploadsDock";
import { installScrollFade } from "@/lib/scrollFade";
import "./index.css";

// Reveal scrollbars while their region is scrolled, so they can fade out at
// rest (see `lib/scrollFade.ts` and the scrollbar block in `index.css`).
//
// Called at module scope rather than from an effect: it is a document-wide
// listener that must exist exactly once for the life of the page, and an effect
// would run twice under React 18 StrictMode. `installScrollFade` is idempotent
// regardless, so a hot reload cannot stack listeners either.
installScrollFade();

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <BrowserRouter>
      <ToastProvider>
        <AuthProvider>
          <JobsProvider>
            {/* Uploads outlive the router's page components, so a transfer
                started on the Files page keeps running after navigating away,
                and the dock is available on every route. */}
            <UploadsProvider>
              <App />
              <UploadsDock />
            </UploadsProvider>
          </JobsProvider>
        </AuthProvider>
      </ToastProvider>
    </BrowserRouter>
  </StrictMode>,
);
