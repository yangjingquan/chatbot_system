import React from "react";
import ReactDOM from "react-dom/client";
import { BrowserRouter } from "react-router-dom";
import { App } from "./App";
import { Chat } from "./Chat";
import { Demo } from "./Demo";
import { ToastProvider } from "./ui";
import "./styles.css";

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <ToastProvider>
      <BrowserRouter>
        {location.pathname === "/chat" ? (
          <Chat />
        ) : location.pathname === "/demo" ? (
          <Demo />
        ) : (
          <App />
        )}
      </BrowserRouter>
    </ToastProvider>
  </React.StrictMode>,
);
