import type { Metadata } from "next";
import { Geist, Geist_Mono } from "next/font/google";

import { Sidebar } from "./_components/Sidebar";
import "./globals.css";

const geistSans = Geist({ variable: "--font-geist-sans", subsets: ["latin"] });
const geistMono = Geist_Mono({ variable: "--font-geist-mono", subsets: ["latin"] });

// every page shows live registry data, so nothing is prerendered at build time
export const dynamic = "force-dynamic";

export const metadata: Metadata = {
  title: "AI Registry Crawler",
  description: "What AI runs in the AWS account, how risky it is, what the agents do and what to fix",
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en">
      <body className={`${geistSans.variable} ${geistMono.variable} antialiased`}>
        <div className="flex min-h-screen">
          <Sidebar />
          <main className="min-w-0 flex-1 px-8 py-8">
            <div className="mx-auto max-w-[1440px]">{children}</div>
          </main>
        </div>
      </body>
    </html>
  );
}
