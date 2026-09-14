import React, { useState } from 'react';

export const StringQuery = ({
  queryName,
  description,
  label,
  options = [],
  validator,
  handleQueryValid,
  handleQueryInvalid,
}) => {
  const [show, setShow] = useState(true);
  const [string, setString] = useState('');
  const datalistId = `${queryName}-options`;

  const handleStringChange = (e) => {
    const value = e.target.value;
    setString(value);
    const valid = validator(value);
    if (valid) {
      return handleQueryValid(queryName, valid);
    }
    handleQueryInvalid(queryName);
  };

  const toggleOption = (option: string) => {
    const nextValue = string === option ? '' : option;
    setString(nextValue);
    if (nextValue) {
      return handleQueryValid(queryName, validator(nextValue));
    }
    handleQueryInvalid(queryName);
  };

  return (
    <div className="mb-10">
      <div className="divider mb-8">
        <div className="tooltip" data-tip={description}>
          <button
            onClick={() => setShow(!show)}
            disabled={!validator(string)}
            className={string ? 'btn btn-success w-80' : 'btn w-80'}
          >
            {label ?? queryName}
          </button>
        </div>
      </div>
      {show && (
        <div className="card bg-neutral text-neutral-content">
          <div className="card-body">
            {options.length > 0 && (
              <div className="mb-4 flex flex-wrap justify-center gap-x-2">
                {options.map((option) => {
                  const isSelected = string.toLowerCase() === option.toLowerCase();
                  return (
                    <label
                      key={option}
                      className={
                        isSelected
                          ? 'flex cursor-pointer items-center justify-center rounded-xl border border-primary bg-primary px-4 py-2 text-xl font-medium text-slate-900'
                          : 'flex cursor-pointer items-center justify-center rounded-xl border border-primary px-4 py-2 text-xl font-medium text-primary'
                      }
                    >
                      <input
                        type="checkbox"
                        checked={isSelected}
                        onChange={() => toggleOption(option)}
                        className="checkbox checkbox-success hidden"
                      />
                      <span className="select-none whitespace-nowrap">{option}</span>
                    </label>
                  );
                })}
              </div>
            )}
            <input
              data-testid="string-input"
              onChange={handleStringChange}
              value={string}
              type="text"
              list={options.length ? datalistId : undefined}
              placeholder={description}
              className="input input-bordered w-full"
            />
            {options.length > 0 && (
              <datalist id={datalistId}>
                {options.map((option) => (
                  <option key={option} value={option} />
                ))}
              </datalist>
            )}
          </div>
        </div>
      )}
    </div>
  );
};

export default StringQuery;
