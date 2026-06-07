import logging

def setup_worker_logging(worker_id: str) -> logging.Logger:
    logger = logging.getLogger(worker_id)
    logger.setLevel(logging.DEBUG)
    
    # Create console handler
    ch = logging.StreamHandler()
    ch.setLevel(logging.DEBUG)
    
    # Create formatter and add it to the handlers
    formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    ch.setFormatter(formatter)
    
    # Add the handlers to the logger
    logger.addHandler(ch)
    
    return logger

worker_logger = setup_worker_logging("file_converter_worker")