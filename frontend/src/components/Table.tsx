// A data table with a sticky header that scrolls inside its card. On a phone
// each row stacks into a card, every cell under its column's name.
import type { Key, ReactNode } from 'react';
import { useT } from '../i18n';

export interface Column<Row> {
  key: string;
  header: ReactNode;
  /** The cell; defaults to the row's field named `key`, as text. */
  cell?: (row: Row) => ReactNode;
  align?: 'start' | 'end';
}

export interface TableProps<Row> {
  caption: string;
  columns: Column<Row>[];
  rows: Row[];
  rowKey: (row: Row) => Key;
  empty?: ReactNode;
  /** Makes a row's first cell a button that opens it. */
  onOpen?: (row: Row) => void;
}

function text(value: unknown): ReactNode {
  if (value === null || value === undefined) return '—';
  return typeof value === 'object' ? JSON.stringify(value) : String(value);
}

export function Table<Row>({ caption, columns, rows, rowKey, empty, onOpen }: TableProps<Row>) {
  const t = useT();
  if (!rows.length) return <p className="empty">{empty ?? t('empty')}</p>;
  return (
    <div className="ui-table-scroll">
      <table className="ui-table">
        <caption className="sr">{caption}</caption>
        <thead>
          <tr>
            {columns.map((column) => (
              <th key={column.key} scope="col" className={column.align === 'end' ? 'end' : undefined}>
                {column.header}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={rowKey(row)}>
              {columns.map((column, i) => {
                const content = column.cell
                  ? column.cell(row)
                  : text((row as Record<string, unknown>)[column.key]);
                return (
                  <td
                    key={column.key}
                    className={column.align === 'end' ? 'end' : undefined}
                    data-label={typeof column.header === 'string' ? column.header : undefined}
                  >
                    {i === 0 && onOpen ? (
                      <button type="button" className="ui-link" onClick={() => onOpen(row)}>
                        {content}
                      </button>
                    ) : (
                      content
                    )}
                  </td>
                );
              })}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
