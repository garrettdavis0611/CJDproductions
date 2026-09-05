// SMTP is configured entirely from environment / GitHub Secrets. Nothing here
// carries a credential.
import nodemailer from 'nodemailer';
import { MAIL_TO, MAIL_FROM } from './config.js';

export function mailerFromEnv() {
  const { SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASS } = process.env;
  if (!SMTP_HOST || !SMTP_USER || !SMTP_PASS) {
    throw new Error('SMTP_HOST, SMTP_USER and SMTP_PASS must be set (see spreads/README.md)');
  }
  const port = Number(SMTP_PORT || 465);
  return nodemailer.createTransport({
    host: SMTP_HOST,
    port,
    secure: port === 465,
    auth: { user: SMTP_USER, pass: SMTP_PASS }
  });
}

export async function sendBoard({ subject, text, html, pdf, filename }) {
  const transport = mailerFromEnv();
  await transport.sendMail({
    from: MAIL_FROM,
    to: MAIL_TO,
    subject,
    text: `${text}\n\nCurrent board attached.`,
    html,
    attachments: pdf ? [{ filename, content: pdf, contentType: 'application/pdf' }] : []
  });
}
