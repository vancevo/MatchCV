import "./globals.css";

export const metadata = { title: "Kho CV IT", description: "Kho CV tập trung cho TalentFlow" };

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return <html lang="vi"><body>{children}</body></html>;
}
