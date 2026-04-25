class DockerServiceError(Exception):
    pass


class UnknownProfileError(DockerServiceError):
    pass


class ContainerNotFoundError(DockerServiceError):
    pass


class ContainerNotReadyError(DockerServiceError):
    pass


class ExecutionTimeoutError(DockerServiceError):
    pass
