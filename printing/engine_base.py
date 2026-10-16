from abc import ABC, abstractmethod

class PrintEngineBase(ABC):
    """
    Абстрактный базовый класс для движков печати.
    """
    
    @abstractmethod
    def print_file(self, filepath: str, printer_name: str, settings: str, copies: int = 1) -> bool:
        """
        Отправляет файл на печать.
        
        :param filepath: Путь к PDF файлу
        :param printer_name: Имя принтера
        :param settings: Строка настройек (например, "fit,paper=A1")
        :param copies: Количество копий
        :return: True если успешно, иначе False
        """
        pass

    @abstractmethod
    def get_name(self) -> str:
        """Возвращает имя движка (например, 'SumatraPDF' или 'Foxit Reader')."""
        pass
