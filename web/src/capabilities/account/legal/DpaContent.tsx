import type { LegalVocabulary } from "./useLegalVocabulary";

export function DpaContent({ vocabulary = {} }: { vocabulary?: LegalVocabulary }) {
  return (
    <>
      <p><em>Effective date: 1 November 2026</em></p>
      <p>This agreement sets out how we handle the medical records you upload to Avodeck. It satisfies Article 28 of the UK General Data Protection Regulation.</p>

      <h2>1. Who this is between</h2>
      <ul>
        <li><strong>You</strong> (the "controller") — the expert witness or organisation that uploads medical records to Avodeck.</li>
        <li><strong>Four Integers Ltd</strong> (the "processor") — a company registered in {vocabulary.company_registration_place}. Dr Aishat Abubakar is the director.</li>
      </ul>

      <h2>2. What this covers</h2>
      <p>This agreement covers all personal data you upload to, create in, or cause to be processed through Avodeck.</p>
      <h3>What we do with it</h3>
      <ul>
        <li>Store your uploaded medical record bundles (encrypted)</li>
        <li>Read each page with optical character recognition</li>
        <li>Use AI to find facts in the records, each linked to the exact words on a numbered page</li>
        <li>Show you the pages and facts so you can check them</li>
        <li>Put together your report from the facts you have verified and the opinions you have written</li>
        <li>Help with later stages: written questions, joint expert meetings, trial bundles and the witness box</li>
        <li>Delete everything securely when the time comes</li>
      </ul>
      <h3>What kind of data</h3>
      <table>
        <thead><tr><th>Category</th><th>Examples</th></tr></thead>
        <tbody>
          <tr><td>Patient health records (special category)</td><td>Consultation notes, test results, prescriptions, referral letters</td></tr>
          <tr><td>Patient identity</td><td>Name, date of birth (sealed with per-case encryption)</td></tr>
          <tr><td>Your dictated notes</td><td>Audio recordings transcribed into text</td></tr>
          <tr><td>Your opinions and report</td><td>Your written analysis and conclusions</td></tr>
          <tr><td>Third-party identity</td><td>Names of the people and organisations who instruct you, firm names, hospital trust names</td></tr>
        </tbody>
      </table>

      <h2>3. Your instructions</h2>
      <p>We process personal data only on your instructions:</p>
      <ol>
        <li>Process data as needed to provide Avodeck, as set out in the terms of service.</li>
        <li>Follow whatever you do in Avodeck (uploading a bundle, creating a case, requesting a deletion).</li>
      </ol>
      <p>We will not use the records for anything else. We do not train AI models on your data, run analytics on patient records, or use them for marketing.</p>

      <h2>4. Confidentiality</h2>
      <p>Everyone who handles personal data on our side is bound by a duty of confidentiality. Our support team cannot see the content of your cases unless you give them a time-limited grant (up to 24 hours), and every access is logged.</p>

      <h2>5. Security</h2>
      <table>
        <thead><tr><th>Measure</th><th>What it means</th></tr></thead>
        <tbody>
          <tr><td>Encryption</td><td>Each case has its own encryption key. Patient names and dates of birth have an extra layer.</td></tr>
          <tr><td>Access control</td><td>Every request checks who you are and what you are allowed to do. The database repeats the check.</td></tr>
          <tr><td>Two-step sign-in</td><td>Required for important actions like signing, serving, exporting or deleting.</td></tr>
          <tr><td>Identifiers removed before AI</td><td>Names and identifying numbers are stripped from the text before it goes to the AI model.</td></tr>
          <tr><td>Audit trail</td><td>Every action is logged in a tamper-resistant chain.</td></tr>
        </tbody>
      </table>

      <h2>6. Who else processes data for us</h2>
      <table>
        <thead><tr><th>Provider</th><th>What for</th><th>Patient data?</th><th>Where</th></tr></thead>
        <tbody>
          <tr><td>Supabase</td><td>Database and sign-in</td><td>Yes (encrypted)</td><td>London</td></tr>
          <tr><td>Amazon Web Services</td><td>File storage, encryption keys, AI</td><td>Encrypted files; de-identified text</td><td>London</td></tr>
          <tr><td>Microsoft Azure</td><td>Reading scanned pages, dictation</td><td>Page images, audio</td><td>UK South</td></tr>
          <tr><td>Railway</td><td>Running the service</td><td>In memory only</td><td>Amsterdam</td></tr>
          <tr><td>Vercel</td><td>Hosting this website</td><td>No</td><td>Global</td></tr>
          <tr><td>Sentry</td><td>Error reports</td><td>No (scrubbed)</td><td>EU</td></tr>
        </tbody>
      </table>
      <p>If we plan to add or change a provider that handles patient data, we will tell you at least <strong>30 days</strong> in advance.</p>

      <h2>7. Helping with data subject rights</h2>
      <p>If a patient asks to see, correct or delete data in one of your cases, that request comes to you as the controller. We will help you respond by providing Avodeck's export and deletion features.</p>

      <h2>8. If something goes wrong</h2>
      <p>If we discover a breach affecting your data, we will tell you within <strong>24 hours</strong> and give you:</p>
      <ul>
        <li>What happened</li>
        <li>What data was affected and roughly how much</li>
        <li>What the likely consequences are</li>
        <li>What we have done or plan to do about it</li>
      </ul>

      <h2>9. Helping with impact assessments</h2>
      <p>If you need to carry out a data protection impact assessment for your use of Avodeck, we will give you the information you need. We maintain our own DPIA, which we can share on request.</p>

      <h2>10. Where data is kept</h2>
      <p>All patient data at rest is stored in the United Kingdom. The only processing outside the UK happens on Railway (Amsterdam, Netherlands), where data passes through container memory only.</p>

      <h2>11. Deletion</h2>
      <p>When a case reaches the end of its retention period, or when you ask us to delete it, we will:</p>
      <ol>
        <li>Destroy the case's encryption key (cryptographic shredding)</li>
        <li>Remove all rows from the database</li>
        <li>Send you a <strong>certificate of deletion</strong></li>
      </ol>
      <p>Before we delete anything, you can export your data. We will keep the export feature available for at least 30 days after you ask to leave.</p>

      <h2>12. Audits</h2>
      <p>On reasonable notice, we will allow you (or someone you appoint) to carry out an audit or inspection.</p>

      <h2>13. Changes</h2>
      <p>We may update this agreement to reflect changes in how we process data or in the law. We will give you at least 30 days' notice.</p>

      <h2>14. Law</h2>
      <p>This agreement is governed by the laws of {vocabulary.company_registration_place}. If there is a conflict between this agreement and the terms of service, this agreement wins on data protection matters.</p>
    </>
  );
}
