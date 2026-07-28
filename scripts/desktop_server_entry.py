from multiprocessing import freeze_support

from pdfmd.desktop_server import run

if __name__ == "__main__":
    freeze_support()
    try:
        run()
    except KeyboardInterrupt:
        pass
