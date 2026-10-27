import { act, fireEvent, render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { useState } from 'react';
import { describe, expect, it, vi } from 'vitest';
import { PrefsProvider } from '../prefs';
import { Dialog, Drawer } from './Dialog';
import { Progress } from './Progress';
import { Table } from './Table';

const wrap = (ui: React.ReactNode) => render(<PrefsProvider>{ui}</PrefsProvider>);

describe('Table', () => {
  const rows = [
    { id: 'a', title: 'ConvLSTM', cited: 9000 },
    { id: 'b', title: '<i>PredRNN</i>', cited: null },
  ];
  const columns = [
    { key: 'title', header: 'Title' },
    { key: 'cited', header: 'Cited', align: 'end' as const },
  ];

  it('renders rows as text and opens one', async () => {
    const open = vi.fn();
    wrap(<Table caption="Papers" columns={columns} rows={rows} rowKey={(r) => r.id} onOpen={open} />);
    expect(screen.getByRole('table', { name: 'Papers' })).toBeInTheDocument();
    expect(screen.getByText('<i>PredRNN</i>')).toBeInTheDocument();
    expect(screen.getByText('—')).toBeInTheDocument();
    await act(() => userEvent.click(screen.getByRole('button', { name: 'ConvLSTM' })));
    expect(open).toHaveBeenCalledWith(rows[0]);
  });

  it('labels each cell with its column for the stacked phone layout', () => {
    wrap(
      <Table
        caption="Papers"
        columns={[...columns, { key: 'x', header: <span>Icon</span> }]}
        rows={rows}
        rowKey={(r) => r.id}
      />,
    );
    const cells = screen.getAllByRole('row')[1]!.querySelectorAll('td');
    expect([...cells].map((c) => c.getAttribute('data-label'))).toEqual(['Title', 'Cited', null]);
  });

  it('says when it is empty', () => {
    wrap(
      <Table
        caption="Papers"
        columns={columns}
        rows={[] as typeof rows}
        rowKey={(r) => r.id}
        empty="No papers"
      />,
    );
    expect(screen.getByText('No papers')).toBeInTheDocument();
  });
});

describe('Dialog and Drawer', () => {
  function Harness({ drawer = false }: { drawer?: boolean }) {
    const [open, setOpen] = useState(true);
    const Kind = drawer ? Drawer : Dialog;
    return (
      <>
        <span data-testid="state">{open ? 'open' : 'closed'}</span>
        <Kind open={open} title="Paper" onClose={() => setOpen(false)}>
          <p>Details</p>
        </Kind>
      </>
    );
  }

  it('shows its title and closes on the button and on Escape', async () => {
    wrap(<Harness />);
    expect(screen.getByRole('heading', { name: 'Paper' })).toBeInTheDocument();
    await act(() => userEvent.click(screen.getByRole('button', { name: 'Close' })));
    expect(screen.getByTestId('state')).toHaveTextContent('closed');
    expect(screen.queryByText('Details')).toBeNull();
  });

  it('closes on cancel (Escape)', () => {
    wrap(<Harness drawer />);
    const dialog = document.querySelector('dialog.ui-drawer')!;
    fireEvent(dialog, new Event('cancel', { cancelable: true }));
    expect(screen.getByTestId('state')).toHaveTextContent('closed');
  });
});

describe('Progress', () => {
  it('reports known progress and ongoing work', () => {
    wrap(
      <>
        <Progress value={3} max={12} label="Screening" />
        <Progress label="Searching" />
      </>,
    );
    const known = screen.getByRole('progressbar', { name: 'Screening' });
    expect(known).toHaveAttribute('aria-valuenow', '3');
    expect(known).toHaveAttribute('aria-valuemax', '12');
    expect(known.querySelector('i')).toHaveStyle({ width: '25.0%' });
    expect(screen.getByRole('progressbar', { name: 'Searching' })).not.toHaveAttribute('aria-valuenow');
  });
});
