// @ts-nocheck

import './globals.css';

export const metadata = {
  title: 'PautaLimpa',
  description: 'Monitoramento legislativo com análises de IA em Next.js',
};

export default function RootLayout({ children }) {
  return (
    <html lang="pt-BR">
      <body>{children}</body>
    </html>
  );
}
