import { Toaster as SonnerToaster } from "sonner";

export function Toaster({ theme }: { theme: "light" | "dark" }) {
  return (
    <SonnerToaster
      theme={theme}
      position="bottom-right"
      visibleToasts={4}
      closeButton
      expand
      gap={10}
      offset={18}
      icons={{
        success: <span className="toast-status-dot toast-success" />,
        error: <span className="toast-status-dot toast-error" />,
        warning: <span className="toast-status-dot toast-warning" />,
        info: <span className="toast-status-dot toast-info" />,
        loading: <span className="toast-status-dot sonner-loader" />,
      }}
      toastOptions={{
        className: "support-toast",
        descriptionClassName: "support-toast-description",
      }}
    />
  );
}
