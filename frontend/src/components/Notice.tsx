import type { NoticeKind } from '../types';

type NoticeProps = {
  message: string;
  kind?: NoticeKind;
  hidden?: boolean;
};

export default function Notice({ message, kind = 'info', hidden = false }: NoticeProps): JSX.Element {
  if (!message && hidden) {
    return (
      <div className="main__notice" id="registerNotice" role="status" aria-live="polite" hidden />
    );
  }

  const classes = ['main__notice'];
  if (kind === 'error') classes.push('main__notice--error');
  if (kind === 'success') classes.push('main__notice--success');

  return (
    <div
      className={classes.join(' ')}
      id="registerNotice"
      role="status"
      aria-live="polite"
      hidden={hidden}
    >
      {message}
    </div>
  );
}
