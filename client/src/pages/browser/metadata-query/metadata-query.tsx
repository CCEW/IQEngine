import React, { useState } from 'react';
import { CLIENT_TYPE_API } from '@/api/Models';
import { useQueryTrack } from '@/api/metadata/queries';
import Results from './results';
import { queries } from './queries';

export const MetadataQuery = () => {
  const [selections, setSelections] = useState(queries);
  const [queryString, setQueryString] = useState('');
  const [geoPositionUpdate, setGeoPositionUpdate] = useState('manual');
  const [selectedTrack, setSelectedTrack] = useState({
    account: '',
    container: '',
    filepath: '',
  });
  const { status, data, error } = useQueryTrack(
    CLIENT_TYPE_API,
    selectedTrack.account,
    selectedTrack.container,
    selectedTrack.filepath
  );

  const toggleSelected = (e) => {
    const name = e.target.name;
    const newSelections = { ...selections };
    newSelections[name].selected = !newSelections[name].selected;
    newSelections[name].value = '';
    setSelections(newSelections);
  };

  const renderQuerySelection = () => {
    return Object.keys(selections).map((item) => {
      return (
        <label key={item} className="cursor-pointer label flex items-center justify-start gap-3 py-1">
          <input
            onChange={toggleSelected}
            type="checkbox"
            name={item}
            checked={selections[item].selected}
            className="checkbox checkbox-success flex-none"
          />
          <span className="label-text whitespace-nowrap">{item}</span>
        </label>
      );
    });
  };

  const renderQueryComponents = () => {
    return Object.keys(selections).map((item) => {
      if (selections[item].selected) {
        const Component = selections[item].component;
        if (item === 'geo') {
          return (
            <Component
              key={item}
              queryName={item}
              validator={selections[item].validator}
              description={selections[item].description}
              handleQueryValid={handleQueryValid}
              handleQueryInvalid={handleQueryInvalid}
              trackData={data ?? []}
              geoPositionUpdate={geoPositionUpdate}
              setGeoPositionUpdate={setGeoPositionUpdate}
            />
          );
        }
        return (
          <Component
            key={item}
            queryName={item}
            validator={selections[item].validator}
            description={selections[item].description}
            handleQueryValid={handleQueryValid}
            handleQueryInvalid={handleQueryInvalid}
          />
        );
      }
    });
  };

  const handleQueryValid = (name: string, value: string) => {
    const newSelections = { ...selections };
    newSelections[name].value = value;
    setSelections(newSelections);
  };

  const handleQueryInvalid = (name: string) => {
    const newSelections = { ...selections };
    newSelections[name].value = '';
    setSelections(newSelections);
  };

  const handleSetSelectedTrack = (account: string, container: string, filepath: string) => {
    setSelectedTrack({
      account: encodeURIComponent(account),
      container: encodeURIComponent(container),
      filepath: encodeURIComponent(filepath),
    });
    setGeoPositionUpdate('track');
  };

  const showQueryButton = () => {
    let empty = true;
    for (let item of Object.keys(selections)) {
      if (selections[item].selected) {
        empty = false;
      }
      if (selections[item].selected && selections[item].value === '') {
        return false;
      }
    }
    if (empty) return false;
    return true;
  };

  const renderResults = () => {
    return (
      <Results
        geoSelected={selections['geo'].selected}
        handleToggleTrack={(account, container, filepath) => handleSetSelectedTrack(account, container, filepath)}
        queryString={queryString}
      />
    );
  };

  const handleQuery = async () => {
    let query = '';
    for (let item of Object.keys(selections)) {
      if (selections[item].value) {
        query += `${selections[item].value}&`;
      }
    }
    if (!query) return;
    setQueryString(query);
  };

  return (
    <div className="ml-10 mt-100">
      <h1 className="text-3xl font-bold mb-4">Regular Query</h1>
      <div className="flex flex-col md:flex-row gap-8 items-start">
        <div className="form-control flex-none w-48 sticky top-4">{renderQuerySelection()}</div>
        <div className="flex-1 min-w-0">
          {renderQueryComponents()}
          <button className="btn btn-success" onClick={handleQuery} disabled={!showQueryButton()}>
            QUERY
          </button>
        </div>
      </div>
      {renderResults()}
    </div>
  );
};

export default MetadataQuery;
