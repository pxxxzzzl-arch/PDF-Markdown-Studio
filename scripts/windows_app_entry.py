if __name__ == "__main__":
    from multiprocessing import freeze_support

    freeze_support()

    from pdfmd.windows_app import run

    raise SystemExit(run())
