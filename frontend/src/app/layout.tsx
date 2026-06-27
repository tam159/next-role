import { Schibsted_Grotesk, Newsreader, JetBrains_Mono } from "next/font/google";
import { NuqsAdapter } from "nuqs/adapters/next/app";
import { Toaster } from "sonner";
import { ThemeProvider } from "@/providers/ThemeProvider";
import { AccentProvider } from "@/providers/AccentProvider";
import "./globals.css";

// Schibsted Grotesk — UI/body sans (all chrome, labels, buttons, chat text).
const sans = Schibsted_Grotesk({
  subsets: ["latin"],
  weight: ["400", "500", "600", "700"],
  variable: "--font-sans",
  display: "swap",
});

// Newsreader — editorial serif, used only for hero/preview display titles.
const serif = Newsreader({
  subsets: ["latin"],
  weight: ["400", "500"],
  style: ["normal", "italic"],
  variable: "--font-serif",
  display: "swap",
});

// JetBrains Mono — file paths, tool names, code/JSON/YAML, attachment chips.
const mono = JetBrains_Mono({
  subsets: ["latin"],
  weight: ["400", "500"],
  variable: "--font-mono",
  display: "swap",
});

// Restore a saved accent before first paint to avoid a flash. Default (no
// attribute) is indigo per globals.css, so this only matters for other accents.
const ACCENT_SCRIPT = `(function(){try{var a=localStorage.getItem('nr-accent');if(a&&['indigo','blue','emerald','coral'].indexOf(a)>=0){document.documentElement.setAttribute('data-accent',a);}}catch(e){}})();`;

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html
      lang="en"
      suppressHydrationWarning
      className={`${sans.variable} ${serif.variable} ${mono.variable}`}
    >
      <head>
        <script dangerouslySetInnerHTML={{ __html: ACCENT_SCRIPT }} />
      </head>
      <body className={sans.className} suppressHydrationWarning>
        <ThemeProvider>
          <AccentProvider>
            <NuqsAdapter>{children}</NuqsAdapter>
          </AccentProvider>
        </ThemeProvider>
        <Toaster />
      </body>
    </html>
  );
}
