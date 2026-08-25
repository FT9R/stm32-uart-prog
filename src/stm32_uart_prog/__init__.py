from . import loggers
from .arg_parser import args

loggers.log = loggers.setup_logger("gen", split=True, level=args.loglvl)
print(f"///// Initialized {__name__} with {str(args).replace('Namespace', 'args',1)}")
