// @ts-nocheck

import './globals.css';
import logoSrc from './logo.png';

export const metadata = {
  title: 'PautaLimpa',
  description: 'Monitoramento legislativo com análises de IA em Next.js',
  icons: {
    icon: logoSrc.src,
    shortcut: logoSrc.src,
    apple: logoSrc.src,
  },
};

export default function RootLayout({ children }) {
  return (
    <html lang="pt-BR">
      <body>{children}</body>
    </html>
  );
}
