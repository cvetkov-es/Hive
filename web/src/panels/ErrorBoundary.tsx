// Граница ошибок. Без неё React на любой ошибке отрисовки размонтирует дерево
// целиком, и диспетчер видит белый экран вместо пульта. Упавший блок говорит,
// что случилось, остальной экран продолжает работать.
//
// `resetKey` снимает ошибку сам, когда меняется то, что блок показывает
// (например, выбрана другая заявка): повторять падение на новых данных незачем.

import { Component, type ReactNode } from 'react';

type Props = {
  children: ReactNode;
  /** Что это за блок, в винительном падеже: «Панель», «Карту и ленту», «Экран». */
  label: string;
  resetKey?: unknown;
  onReset?: () => void;
  /** Сообщение карточкой по центру экрана — для окон и для карты под
   *  плавающими панелями, где обычное сообщение оказалось бы не видно. */
  float?: boolean;
};

export class ErrorBoundary extends Component<Props, { error: Error | null }> {
  state: { error: Error | null } = { error: null };

  static getDerivedStateFromError(error: Error) {
    return { error };
  }

  componentDidCatch(error: Error) {
    console.error(error);
  }

  componentDidUpdate(prev: Props) {
    if (this.state.error && prev.resetKey !== this.props.resetKey) {
      this.setState({ error: null });
    }
  }

  render() {
    const { error } = this.state;
    if (!error) return this.props.children;
    const { label, onReset, float } = this.props;
    return (
      <div className={`explain ${float ? 'boundary-float' : ''}`} role="alert">
        <div className="un-reason">
          {label} не удалось показать: ошибка в интерфейсе ({error.message}).
        </div>
        <div className="note" style={{ marginTop: 4 }}>
          Остальной экран работает: выберите другую заявку или снимите выбор.
        </div>
        {onReset && (
          <button style={{ marginTop: 8 }}
                  onClick={() => { this.setState({ error: null }); onReset(); }}>
            Сбросить выбор
          </button>
        )}
      </div>
    );
  }
}
