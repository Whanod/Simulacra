"use client";

import Sidebar from "@/components/shell/Sidebar";
import ToastProvider from "@/components/feedback/ToastProvider";
import StudioWalletProvider from "@/components/wallet/StudioWalletProvider";
import QueryProvider from "@/components/providers/QueryProvider";
import StudioStoreProvider from "@/lib/state/useStudioStore";

export default function StudioLayout({ children }: { children: React.ReactNode }) {
  return (
    <StudioWalletProvider>
      <QueryProvider>
        <StudioStoreProvider>
          <ToastProvider>
            <Sidebar />
            <div id="main">
              {children}
            </div>
          </ToastProvider>
        </StudioStoreProvider>
      </QueryProvider>
    </StudioWalletProvider>
  );
}
