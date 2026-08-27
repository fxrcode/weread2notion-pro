import logging
import os
import tempfile
import unittest

from weread2notionpro.logging_config import configure_logging


class LoggingConfigTest(unittest.TestCase):
    def test_configures_package_logger_once(self):
        package_logger = logging.getLogger("weread2notionpro")
        original_handlers = package_logger.handlers[:]
        original_level = package_logger.level
        original_propagate = package_logger.propagate
        package_logger.handlers.clear()

        try:
            with tempfile.TemporaryDirectory() as log_dir:
                log_file = configure_logging(log_dir)
                second_result = configure_logging(log_dir)

                self.assertTrue(os.path.exists(log_file))
                self.assertIsNone(second_result)
                self.assertEqual(len(package_logger.handlers), 2)
                for handler in package_logger.handlers:
                    handler.close()
        finally:
            package_logger.handlers[:] = original_handlers
            package_logger.setLevel(original_level)
            package_logger.propagate = original_propagate


if __name__ == "__main__":
    unittest.main()
