using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Drawing;
using System.IO;
using System.Net;
using System.Reflection;
using System.Runtime.InteropServices;
using System.Text;
using System.Threading;
using System.Windows.Forms;

[assembly: AssemblyTitle("Raven Portable")]
[assembly: AssemblyProduct("Raven")]
[assembly: AssemblyDescription("Portable spouštěč Raven")]
[assembly: AssemblyCompany("Raven")]
[assembly: AssemblyVersion("1.2.0.0")]
[assembly: AssemblyFileVersion("1.2.0.0")]

internal sealed class RavenLauncherForm : Form
{
    private delegate bool EnumWindowsProc(IntPtr window, IntPtr parameter);
    [DllImport("user32.dll")]
    private static extern bool EnumWindows(EnumWindowsProc callback, IntPtr parameter);
    [DllImport("user32.dll")]
    private static extern bool IsWindowVisible(IntPtr window);
    [DllImport("user32.dll")]
    private static extern uint GetWindowThreadProcessId(IntPtr window, out uint processId);
    private readonly Label status;
    private readonly ProgressBar progress;
    private readonly string diagnosticLog = Path.Combine(AppDomain.CurrentDomain.BaseDirectory, "Raven Portable.log");

    internal RavenLauncherForm()
    {
        Text = "Raven Portable 1.2";
        Icon = Icon.ExtractAssociatedIcon(Application.ExecutablePath);
        ClientSize = new Size(440, 125);
        FormBorderStyle = FormBorderStyle.FixedDialog;
        MaximizeBox = false;
        MinimizeBox = false;
        StartPosition = FormStartPosition.CenterScreen;
        TopMost = true;
        BackColor = Color.FromArgb(19, 22, 31);

        status = new Label();
        status.Text = "Raven se spouští z flash disku…";
        status.ForeColor = Color.White;
        status.Font = new Font("Segoe UI", 12F, FontStyle.Regular);
        status.AutoSize = false;
        status.TextAlign = ContentAlignment.MiddleCenter;
        status.SetBounds(20, 18, 400, 48);
        Controls.Add(status);

        progress = new ProgressBar();
        progress.Style = ProgressBarStyle.Marquee;
        progress.MarqueeAnimationSpeed = 25;
        progress.SetBounds(35, 78, 370, 18);
        Controls.Add(progress);

        Shown += delegate { new Thread(StartRaven) { IsBackground = true }.Start(); };
    }

    private static string FindRavenRoot()
    {
        string baseDir = AppDomain.CurrentDomain.BaseDirectory.TrimEnd(Path.DirectorySeparatorChar);
        var candidates = new List<string>();
        if (IsRavenRoot(baseDir)) candidates.Add(baseDir);
        foreach (string dir in Directory.GetDirectories(baseDir))
            if (IsRavenRoot(dir)) candidates.Add(dir);
        if (candidates.Count == 0)
            throw new InvalidOperationException("Vedle spouštěče nebyla nalezena kompletní složka Raven.");
        if (candidates.Count > 1)
            throw new InvalidOperationException("Vedle spouštěče je více složek Raven. Ponechte pouze jednu aktuální verzi.");
        return Path.GetFullPath(candidates[0]).TrimEnd(Path.DirectorySeparatorChar);
    }

    private static bool IsRavenRoot(string path)
    {
        return File.Exists(Path.Combine(path, "spustit-raven.ps1"))
            && File.Exists(Path.Combine(path, "raven_control.py"))
            && (File.Exists(Path.Combine(path, "Raven.exe"))
                || File.Exists(Path.Combine(path, "desktop", "Raven-Desktop.exe")));
    }

    private static bool HasVisibleDesktopWindow(string desktopExecutable)
    {
        string expected = Path.GetFullPath(desktopExecutable);
        foreach (Process process in Process.GetProcessesByName(Path.GetFileNameWithoutExtension(expected)))
        {
            try
            {
                if (!String.Equals(Path.GetFullPath(process.MainModule.FileName), expected, StringComparison.OrdinalIgnoreCase))
                    continue;
                bool visible = false;
                EnumWindows(delegate(IntPtr window, IntPtr parameter)
                {
                    uint owner;
                    GetWindowThreadProcessId(window, out owner);
                    if (owner == (uint)process.Id && IsWindowVisible(window))
                    {
                        visible = true;
                        return false;
                    }
                    return true;
                }, IntPtr.Zero);
                if (visible) return true;
            }
            catch { }
            finally { process.Dispose(); }
        }
        return false;
    }

    private static string Quote(string value)
    {
        return "\"" + value.Replace("\"", "\\\"") + "\"";
    }

    private void SetStatus(string text)
    {
        BeginInvoke((MethodInvoker)delegate { status.Text = text; });
    }

    private void StartRaven()
    {
        try
        {
            File.WriteAllText(diagnosticLog, DateTime.Now.ToString("s") + " Raven Portable start\r\n", Encoding.UTF8);
            string root = FindRavenRoot();
            File.AppendAllText(diagnosticLog, "Root: " + root + "\r\n", Encoding.UTF8);
            string systemRoot = Environment.GetEnvironmentVariable("SystemRoot") ?? @"C:\Windows";
            string powershell = Path.Combine(systemRoot, "System32", "WindowsPowerShell", "v1.0", "powershell.exe");
            if (!File.Exists(powershell)) throw new FileNotFoundException("Windows PowerShell nebyl nalezen.", powershell);

            SetStatus("Spouštím služby a lokální model…");
            var info = new ProcessStartInfo();
            info.FileName = powershell;
            info.Arguments = "-NoProfile -NonInteractive -ExecutionPolicy Bypass -File " + Quote(Path.Combine(root, "spustit-raven.ps1")) + " -NoErrorPopup";
            info.WorkingDirectory = root;
            info.UseShellExecute = false;
            info.CreateNoWindow = true;
            info.RedirectStandardOutput = false;
            info.RedirectStandardError = false;

            using (Process process = Process.Start(info))
            {
                if (!process.WaitForExit(600000))
                {
                    try { process.Kill(); } catch { }
                    throw new TimeoutException("Spuštění Ravenu trvalo déle než 10 minut.");
                }
                if (process.ExitCode != 0)
                    throw new InvalidOperationException("Spouštěcí skript skončil chybou. Podrobnosti: " + Path.Combine(root, "runtime", "logs", "launcher.log"));
            }

            SetStatus("Ověřuji hlavní okno Ravenu…");
            DateTime deadline = DateTime.UtcNow.AddSeconds(75);
            bool healthy = false;
            bool desktopVisible = false;
            // Match the production shell selection in spustit-raven.ps1.
            string desktopExecutable = Path.Combine(root, "Raven.exe");
            if (!File.Exists(desktopExecutable))
                desktopExecutable = Path.Combine(root, "desktop", "Raven-Desktop.exe");
            while (DateTime.UtcNow < deadline)
            {
                try
                {
                    var request = (HttpWebRequest)WebRequest.Create("http://127.0.0.1:8126/settings");
                    request.Timeout = 2500;
                    request.ReadWriteTimeout = 2500;
                    using (var response = (HttpWebResponse)request.GetResponse())
                        healthy = response.StatusCode == HttpStatusCode.OK;
                }
                catch { healthy = false; }
                desktopVisible = HasVisibleDesktopWindow(desktopExecutable);
                if (healthy && desktopVisible) break;
                Thread.Sleep(750);
            }
            if (!healthy) throw new InvalidOperationException("Služba Raven po spuštění neodpovídá.");
            if (!desktopVisible) throw new InvalidOperationException("Služby běží, ale hlavní okno Raven se nezobrazilo. Podrobnosti jsou v runtime\\electron-main.log.");

            File.AppendAllText(diagnosticLog, DateTime.Now.ToString("s") + " Raven odpovídá a hlavní okno je viditelné. Launcher končí.\r\n", Encoding.UTF8);
            Environment.ExitCode = 0;
            BeginInvoke((MethodInvoker)delegate { Close(); });
        }
        catch (Exception ex)
        {
            try { File.AppendAllText(diagnosticLog, DateTime.Now.ToString("s") + " CHYBA: " + ex + "\r\n", Encoding.UTF8); } catch { }
            BeginInvoke((MethodInvoker)delegate
            {
                progress.Style = ProgressBarStyle.Blocks;
                status.Text = "Raven se nepodařilo spustit.";
                MessageBox.Show(this, ex.Message, "Raven Portable – chyba", MessageBoxButtons.OK, MessageBoxIcon.Error);
                Close();
            });
        }
    }
}

internal static class Program
{
    [STAThread]
    private static void Main()
    {
        // Closing the launcher early, or any startup error, is not success.
        Environment.ExitCode = 1;
        Application.EnableVisualStyles();
        Application.SetCompatibleTextRenderingDefault(false);
        Application.Run(new RavenLauncherForm());
    }
}
