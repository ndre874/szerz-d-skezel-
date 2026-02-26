import sys
from PyQt5.QtWidgets import QApplication, QSplashScreen
from PyQt5.QtCore import QTimer

# Show loading screen immediately on application start
app = QApplication(sys.argv)
splash = QSplashScreen()
splash.show()

# Defer heavy work after showing splash
QTimer.singleShot(0, lambda: (init_db(), splash.close(), Main()))

sys.exit(app.exec_())