export function PrivacyContent() {
  return (
    <>
      <p><em>Effective date: 18 October 2026</em></p>
      <p>Avodeck helps expert witnesses review medical records. This policy explains what personal data we hold, why, where it is kept, and the choices you have.</p>

      <h2>1. Who we are</h2>
      <p>Avodeck is made by Four Integers Ltd, a company registered in England and Wales. Dr Aishat Abubakar is the director.</p>
      <ul>
        <li><strong>Address:</strong> 82a James Carter Road, Mildenhall, IP28 7DE, England, UK</li>
        <li><strong>Email:</strong> hello@fourintegers.com</li>
        <li><strong>Data protection contact:</strong> Dr Aishat Abubakar (hello@fourintegers.com)</li>
      </ul>

      <h2>2. Our role: controller and processor</h2>
      <ul>
        <li><strong>Your account and this website.</strong> For your name, email, sign-in details and billing records, we are the <em>controller</em>.</li>
        <li><strong>The records in your cases.</strong> When you upload medical records, you are the <em>controller</em> and we are your <em>processor</em>. We only handle those records to provide Avodeck to you.</li>
      </ul>

      <h2>3. Data we collect</h2>
      <h3>3.1 When you join the pilot</h3>
      <ul>
        <li>Your name and email address</li>
        <li>Roughly how many cases you take on a year, if you tell us</li>
      </ul>
      <h3>3.2 Your account</h3>
      <ul>
        <li>Name, email address and professional details</li>
        <li>Sign-in and security information</li>
        <li>Billing and invoice records</li>
      </ul>
      <h3>3.3 Your cases</h3>
      <ul>
        <li>The medical records you upload, and the text we read from them</li>
        <li>The facts Avodeck finds, and which ones you have checked</li>
        <li>Your notes, your report, your dictation and your signature</li>
        <li>A record of who did what in each case</li>
      </ul>
      <h3>3.4 Technical data</h3>
      <ul>
        <li>IP address, browser and device type</li>
        <li>Access and security logs (never containing medical record text)</li>
      </ul>

      <h2>4. How we use it, and our legal bases</h2>
      <ul>
        <li><strong>To provide Avodeck</strong> (contract): running your account, reading your records, producing your report.</li>
        <li><strong>To keep Avodeck secure</strong> (legitimate interests): spotting unusual sign-ins, preventing misuse, fixing faults.</li>
        <li><strong>To meet legal duties</strong> (legal obligation): keeping invoices and tax records.</li>
        <li><strong>To tell you about the pilot</strong> (consent): you can ask us to stop at any time.</li>
      </ul>

      <h2>5. How Avodeck uses AI</h2>
      <ul>
        <li><strong>AI finds and links facts. It never writes your opinion.</strong> Every suggestion points to the exact words on a numbered page, and you check each one.</li>
        <li><strong>Names and identifying numbers are removed</strong> from the text before it is sent to an AI model.</li>
        <li><strong>No training on your data.</strong> Our AI providers do not use your records to train their models.</li>
      </ul>

      <h2>6. Who processes data for us</h2>
      <table>
        <thead><tr><th>Provider</th><th>What for</th><th>Where</th></tr></thead>
        <tbody>
          <tr><td>Supabase</td><td>Database, sign-in and file storage</td><td>London, UK</td></tr>
          <tr><td>Railway</td><td>Running the Avodeck service</td><td>Amsterdam, NL</td></tr>
          <tr><td>Amazon Web Services</td><td>AI that finds facts in the records</td><td>London, UK</td></tr>
          <tr><td>Microsoft Azure</td><td>Reading scanned pages and dictation</td><td>UK South</td></tr>
          <tr><td>Resend</td><td>Sending emails</td><td>See provider</td></tr>
          <tr><td>Stripe</td><td>Payments</td><td>See provider</td></tr>
          <tr><td>Sentry</td><td>Error reports</td><td>EU</td></tr>
          <tr><td>Vercel</td><td>Hosting this website</td><td>See provider</td></tr>
        </tbody>
      </table>

      <h2>7. Where your data is kept</h2>
      <p>All patient data at rest is stored in the United Kingdom. The Avodeck service runs on Railway in the Netherlands — data passes through its servers in memory only and nothing is saved to disk there.</p>

      <h2>8. How we keep it safe</h2>
      <ul>
        <li>Encryption in transit and at rest</li>
        <li>Two-step sign-in for every account</li>
        <li>Each case is visible only to the expert and the people they add</li>
        <li>Patient details are kept out of our logs and error reports</li>
        <li>Regular security testing</li>
      </ul>

      <h2>9. How long we keep it</h2>
      <table>
        <thead><tr><th>Data</th><th>How long</th></tr></thead>
        <tbody>
          <tr><td>Case records and reports</td><td>As long as you set for each case; deleted with a certificate when the time is up</td></tr>
          <tr><td>Account data</td><td>While your account is open</td></tr>
          <tr><td>Billing records</td><td>6 years (tax law)</td></tr>
          <tr><td>Pilot sign-ups</td><td>Until the pilot ends, or sooner if you ask</td></tr>
        </tbody>
      </table>

      <h2>10. Your rights</h2>
      <p>You have the right to see, correct, delete, take with you, object to, or limit how we use your data. You can withdraw consent at any time. You may complain to the Information Commissioner's Office (ico.org.uk).</p>
      <p>To use any of these rights, email hello@fourintegers.com. We aim to respond within one month.</p>

      <h2>11. Cookies</h2>
      <p>This website does not use advertising or tracking cookies. Avodeck uses one essential cookie to keep you signed in securely.</p>

      <h2>12. If something goes wrong</h2>
      <p>If a breach affects your personal data, we will tell you without undue delay and, where required, tell the ICO within 72 hours.</p>

      <h2>13. Changes to this policy</h2>
      <p>We may update this policy from time to time. If we make an important change, we will tell you by email or in Avodeck before it takes effect.</p>

      <h2>14. Contact us</h2>
      <p>For any question about privacy, email hello@fourintegers.com or write to Four Integers Ltd, 82a James Carter Road, Mildenhall, IP28 7DE, England, UK.</p>
    </>
  );
}
