using System;
using System.Drawing;
using System.Windows.Forms;

namespace PesuAgent.Host {
    public class PesuNonActivatingForm : Form {
        protected override CreateParams CreateParams {
            get {
                CreateParams cp = base.CreateParams;
                cp.ExStyle |= 0x08000000; // WS_EX_NOACTIVATE
                cp.ExStyle |= 0x00000080; // WS_EX_TOOLWINDOW
                return cp;
            }
        }

        protected override bool ShowWithoutActivation {
            get { return true; }
        }
    }
}
